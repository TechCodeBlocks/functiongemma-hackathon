import sys


sys.path.insert(0, "cactus/python/src")
functiongemma_path = "cactus/weights/functiongemma-270m-it"

import json, os, time, re
from cactus import cactus_init, cactus_complete, cactus_destroy
from google import genai
from google.genai import types

def clean_json_string(raw_str):
    """Clean up common JSON generation errors."""
    s = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', raw_str)
    
    # Try to fix trailing commas
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    
    # Fix missing closing braces
    open_b = s.count('{') - s.count('}')
    if open_b > 0:
        s += '}' * open_b
    open_sq = s.count('[') - s.count(']')
    if open_sq > 0:
        s += ']' * open_sq
        
    return s

def parse_and_validate(raw_str, tools):
    """Parse JSON and validate/coerce against tool schema."""
    tools_by_name = {t["name"]: t for t in tools}
    
    cleaned_str = clean_json_string(raw_str)
    
    try:
        raw = json.loads(cleaned_str)
    except json.JSONDecodeError:
        # Fallback regex parsing
        matches = re.finditer(r'"name"\s*:\s*"([^"]+)"\s*,\s*"arguments"\s*:\s*({[^}]+})', cleaned_str)
        calls = []
        for m in matches:
            name, args_str = m.groups()
            try:
                args = json.loads(args_str)
                calls.append({"name": name, "arguments": args})
            except:
                pass
        raw = {"function_calls": calls}

    # Ensure function_calls is a list
    calls_raw = raw.get("function_calls", [])
    if isinstance(calls_raw, dict):
        calls_raw = [calls_raw]
    if not isinstance(calls_raw, list):
        calls_raw = []

    valid_calls = []
    
    for call in calls_raw:
        if not isinstance(call, dict) or "name" not in call or "arguments" not in call:
            continue
            
        name = call["name"]
        args = call["arguments"]
        
        if name not in tools_by_name:
            continue
            
        tool = tools_by_name[name]
        schema = tool.get("parameters", {})
        props = schema.get("properties", {})
        
        valid_args = {}
        for prop_name, prop_info in props.items():
            if prop_name in args:
                val = args[prop_name]
                prop_type = prop_info.get("type", "").lower()
                
                try:
                    if prop_type in ("integer", "number"):
                        # Extract numerical parts if given a string with text
                        if isinstance(val, str):
                            nums = re.findall(r'-?\d+\.?\d*', val)
                            if nums:
                                val = float(nums[0]) if "." in nums[0] else int(nums[0])
                        
                        # Coerce and ensure positive
                        if prop_type == "integer":
                            valid_args[prop_name] = abs(int(float(val)))
                        else:
                            valid_args[prop_name] = abs(float(val))
                    elif prop_type == "string":
                        valid_args[prop_name] = str(val).strip()
                    else:
                        valid_args[prop_name] = val
                except (ValueError, TypeError):
                    continue
                    
        # Verify required arguments
        required = schema.get("required", [])
        if all(r in valid_args for r in required):
            valid_calls.append({"name": name, "arguments": valid_args})
            
    return {
        "function_calls": valid_calls,
        "total_time_ms": raw.get("total_time_ms", 0),
        "confidence": raw.get("confidence", 0),
    }

def generate_cactus(messages, tools):
    """Run function calling on-device via FunctionGemma + Cactus."""
    model = cactus_init(functiongemma_path)

    cactus_tools = [{
        "type": "function",
        "function": t,
    } for t in tools]
   
    raw_str = cactus_complete(
        model,
        messages, # The required system prompt is already passed in cactus_complete below as: [{"role": "system", "content": "You are a helpful assistant that can use tools"}] + messages
        tools=cactus_tools,
        force_tools=True,
        max_tokens=256,
        stop_sequences=["<|im_end|>", "<end_of_turn>"]
    )
    print(raw_str)
    
    cactus_destroy(model)

    return parse_and_validate(raw_str, tools)

def generate_cloud(messages, tools):
    """Run function calling via Gemini Cloud API."""
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    gemini_tools = [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        k: types.Schema(type=v["type"].upper(), description=v.get("description", ""))
                        for k, v in t["parameters"]["properties"].items()
                    },
                    required=t["parameters"].get("required", []),
                ),
            )
            for t in tools
        ])
    ]

    contents = [m["content"] for m in messages if m["role"] == "user"]

    start_time = time.time()

    gemini_response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(tools=gemini_tools),
    )

    total_time_ms = (time.time() - start_time) * 1000

    function_calls = []
    for candidate in gemini_response.candidates:
        for part in candidate.content.parts:
            if part.function_call:
                function_calls.append({
                    "name": part.function_call.name,
                    "arguments": dict(part.function_call.args),
                })

    return {
        "function_calls": function_calls,
        "total_time_ms": total_time_ms,
    }

def generate_hybrid(messages, tools, confidence_threshold=0.99):
    """Hybrid inference strategy using intent separation and a multiprocessing quorum approach."""
    import concurrent.futures
    import json
    import re
    
    def dict_hash(d):
        return json.dumps(d, sort_keys=True)
        
    system_messages = [{"role": "system", "content": "You are a helpful assistant that can use tools. Do not ask for clarification. Just do your best. Be creative, for example waking people up needs an alarm. Don't add extra punctuation or verbosity to parameters, interpret the user's intent as literal."}] + messages
    
    def get_top_k_tools(query, tools_list, k=2):
        doc_q = nlp(query.lower())
        q_lemmas = set(token.lemma_ for token in doc_q if not token.is_stop and token.is_alpha)
        
        synonyms = {
            "wake": ["alarm"], "text": ["message", "send"],
            "find": ["search", "contact"], "look": ["search", "contact"],
            "music": ["play", "song"], "weather": ["temperature"],
            "remind": ["reminder"], "call": ["contact"]
        }
        expanded_q = set(q_lemmas)
        for lemma in q_lemmas:
            if lemma in synonyms:
                expanded_q.update(synonyms[lemma])
                
        scored = []
        for t in tools_list:
            t_text = f"{t['name']} {t['description']}"
            if "parameters" in t and "properties" in t["parameters"]:
                for prop_name, prop_info in t["parameters"]["properties"].items():
                    t_text += f" {prop_name} {prop_info.get('description', '')}"
            doc_t = nlp(t_text.lower())
            t_lemmas = set(token.lemma_ for token in doc_t if not token.is_stop and token.is_alpha)
            score = len(expanded_q & t_lemmas)
            scored.append((score, t))
            
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for score, t in scored[:k]]

    # 1. Intent separation
    intents = []
    base_messages = [m for m in system_messages if m["role"] != "user"]
    user_messages = [m for m in system_messages if m["role"] == "user"]
    
    if user_messages:
        last_user_msg = user_messages[-1]
        content = last_user_msg["content"]
        
        # Split on common conjunctions/punctuation that denote distinct commands
        parts = re.split(r'(?i)\b(?:and|then)\b|,', content)
        parts = [p.strip() for p in parts if p.strip()]
        
        if len(parts) > 0:
            for part in parts:
                intent_msgs = base_messages[:]
                intent_msgs.extend(user_messages[:-1])
                intent_msgs.append({"role": "user", "content": part})
                top_tools = get_top_k_tools(part, tools, k=2)
                intents.append((intent_msgs, top_tools))
        else:
            intents.append((system_messages, tools))
    else:
        intents.append((system_messages, tools))

    all_combined_calls = []
    total_time_ms = 0
    overall_source = "on-device"
    
    # We use ProcessPoolExecutor for true parallelism
    executor = concurrent.futures.ProcessPoolExecutor(max_workers=3)
    
    for intent_msgs, intent_tools in intents:
        counts = {}
        best_res = None
        first_res = None
        
        futures = [executor.submit(generate_cactus, intent_msgs, intent_tools) for _ in range(3)]
        
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result()
                calls = res.get("function_calls", [])
                
                key = dict_hash(calls)
                
                if first_res is None:
                    first_res = res
                
                if key not in counts:
                    counts[key] = {"count": 1, "res": res}
                else:
                    counts[key]["count"] += 1
                    
                if counts[key]["count"] >= 2:
                    best_res = counts[key]["res"]
                    best_res["source"] = "on-device (quorum)"
                    break
            except Exception:
                pass
                
        # Try to clean up running futures if quorum was reached early
        for f in futures:
            f.cancel()
            
        if best_res is not None:
            all_combined_calls.extend(best_res.get("function_calls", []))
            total_time_ms += best_res.get("total_time_ms", 0)
        elif first_res is not None:
            all_combined_calls.extend(first_res.get("function_calls", []))
            total_time_ms += first_res.get("total_time_ms", 0)
            if overall_source == "on-device":
                overall_source = "on-device (no quorum)"
                
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        # Pre-Python 3.9 fallback
        executor.shutdown(wait=False)

    return {
        "function_calls": all_combined_calls,
        "source": overall_source,
        "total_time_ms": total_time_ms,
        "confidence": 1.0 # mock confidence
    }

def print_result(label, result):
    """Pretty-print a generation result."""
    print(f"\\n=== {label} ===\\n")
    if "source" in result:
        print(f"Source: {result['source']}")
    if "confidence" in result:
        print(f"Confidence: {result['confidence']:.4f}")
    if "local_confidence" in result:
        print(f"Local confidence (below threshold): {result['local_confidence']:.4f}")
    print(f"Total time: {result['total_time_ms']:.2f}ms")
    for call in result.get("function_calls", []):
        print(f"Function: {call['name']}")
        print(f"Arguments: {json.dumps(call['arguments'], indent=2)}")

############## Example usage ##############

if __name__ == "__main__":
    tools = [{
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name",
                }
            },
            "required": ["location"],
        },
    }]

    messages = [
        {"role": "user", "content": "What is the weather in San Francisco?"}
    ]

    on_device = generate_cactus([{"role": "system", "content": "You are a helpful assistant that can use tools"}] + messages, tools)
    print_result("FunctionGemma (On-Device Cactus)", on_device)

    cloud = generate_cloud(messages, tools)
    print_result("Gemini (Cloud)", cloud)

    hybrid = generate_hybrid(messages, tools)
    print_result("Hybrid (On-Device + Cloud Fallback)", hybrid)
