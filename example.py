
############## Don't change setup ##############

import sys
sys.path.insert(0, "cactus/python/src")
functiongemma_path = "cactus/weights/functiongemma-270m-it"


############## Using Cactus ##############

from cactus import cactus_init, cactus_complete, cactus_destroy
import json

model = cactus_init(functiongemma_path)

tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City name"
                }
            },
            "required": ["location"]
        }
    }
}]

messages = [
    {"role": "system", "content": "You are a helpful assistant that can use tools."},
    {"role": "user", "content": "What is the weather in San Francisco?"}
]

response = json.loads(cactus_complete(
    model,
    messages,
    tools=tools,
    force_tools=True,
    max_tokens=256,
    stop_sequences=["<|im_end|>", "<end_of_turn>"]
))

cactus_destroy(model)

############## Print resonse and function call ############## 

print("\n=== Full Response ===\n")
print(json.dumps(response, indent=2))

print("\n=== Function Calls ===\n")
for call in response.get("function_calls", []):
    print(f"Function: {call['name']}")
    print(f"Arguments: {json.dumps(call['arguments'], indent=2)}")