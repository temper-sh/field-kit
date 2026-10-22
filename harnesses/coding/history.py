"""Task feedback; Field Kit owns model-format parsing and history serialization."""
import json


def feedback_messages(response, feedback, adapter):
    assistant = adapter.assistant_message(response)
    content = json.dumps(feedback, ensure_ascii=False)
    calls = assistant.get("tool_calls", [])
    returned = ([{"role": "tool", "tool_call_id": call["id"], "content": content}
                 for call in calls] if calls else [{"role": "user", "content": content}])
    return [assistant, *returned]
