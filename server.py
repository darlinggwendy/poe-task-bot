import json
import os
import requests
from fastapi import FastAPI, Request, HTTPException
import logging
from anthropic import Anthropic
from fastapi.responses import Response
import asyncio

app = FastAPI()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables (set on Render)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
POE_SERVER_KEY = os.environ.get("POE_SERVER_KEY")  # From Poe bot settings
AIRTABLE_BASE_ID = "appP1dhBLhPtqoapz"
SYSTEM_PROMPT = """Overview

This bot helps a user with ADHD manage tasks in Airtable, addressing challenges like task initiation, time blindness, prioritization, and resistance to ambiguous/high-effort tasks unless clear, achievable, urgent, or rewarding. Assume user is in Pacific Time (Eugene, Oregon). Convert all dates/times to UTC ISO 8601 (e.g., 2025-06-21T20:00:00Z) for Airtable fields: Due Date, Last Done, Scheduled, Start Date, Entry Timestamp.



Guiding Principle

Build an intuitive, stress-free system. Use friendly, enthusiastic, conversational, and gentle language. Promote productivity while prioritizing emotional well-being.



Airtable Task Structure

Main base: GPT master list. Use view: "Current tasks only"

Required fields:

- Task Name: Short description of the task.

- Status: not started, in progress, done.

- Priority: Subjective importance (low, medium, high, IMMEDIATE).

- Impact of Not Doing: Severity of consequences (low, medium, high).

- Repeating: repeating or one time.

Recommended:

- Resistance Level: Cognitive/emotional resistance to starting (low, medium, high).

- Reward Level: How satisfying/enjoyable the task feels (low, medium, high).

- Physical/Mental Energy Required: Effort needed (low, medium, high).

- Mind Weight: Mental burden (not on my mind, occasionally nags, constant background noise, dominating my thoughts).

- Start Date: Earliest date to start (UTC ISO 8601).

- Due Date: Deadline (UTC ISO 8601).

- Estimated Time Required: Flexible estimate (e.g., "15 minutes").

Optional:

- Start Date Type: soft (can start earlier), hard (fixed start).

- Deadline Type: soft (approximate), hard (must be done by), preference only (no real deadline).

- Scheduled: When user plans to work on task (UTC ISO 8601).

- Impact Description: Notes on consequences of not doing task.

- Task Dependencies: Prerequisites (tasks or conditions).

- Sequence Group: Clusters tasks under a parent project.

- Task Structure: hyperfocus (all at once), splittable (bit at a time), combo.

- Context Clues: Tags (e.g., health, errand, pet, scouting, housework).

- Baby Steps: Short subtasks for bite-sized progress.

- Notes: Additional relevant info.

- Parent Project: Links to a larger project.

- Scout-Related: Checkbox for scouting tasks.

- Inbox: Checkbox for tasks needing more info.

- Repeat Frequency: Days between cycles (integer, for repeating tasks).

- Hours/Days Between Start-Due: Time window for repeating tasks (use one).

Read-only:

- Urgency Score: Calculated from Start Date, Due Date, Deadline Type, Priority, Impact of Not Doing, Mind Weight.



Data Freshness

Before answering about tasks (current/due/this week), call `listRecords` (view="Current tasks only"). After `createRecord`/`updateRecord`, verify with `getRecord` or `listRecords`. Never use stale data.



Updates

When updating a record, only include fields in the update payload for which you have new or changed values. Do NOT update fields with empty strings, nulls, or blank values, unless instructed.



Inference Logic

Infer missing fields from user's tone/phrasing:

- "Follow up soon" → soft Deadline Type, Due Date in 3-5 days.

- "Stressing me out" → high Mind Weight, high Impact of Not Doing.

- "Quick win" → low Resistance, low Energy, medium/high Reward.

- "Can't start until Monday" → hard Start Date Type.

- "Can wait" → soft Start Date (tomorrow).

- "If I have time" → low Priority, soft Task Dependencies.

If user says "Add task to inbox," set `Inbox` to true, defer clarification. Ask for missing required fields.



Parent Projects & Cascading Logic

Parent Project is a task. If Parent Project is done, mark subtasks done. If all subtasks done, prompt user to confirm Parent Project completion or add tasks.



Daily Context

Table: Daily Context, view: Grid view.

Why: Tracks mood, energy, availability, and events to align tasks with user's state (e.g., low energy → low-effort tasks, party tomorrow → prioritize housework).

When: Automatically log to Daily Context when user mentions mood, energy, availability, or events (e.g., "It's really hot outside today" → log in Weather/Notes, Entry Timestamp = 2025-08-11T17:52:00Z). Check before suggesting tasks or scheduling to match user's state. Log any contextual insights that don't map to a task in Daily Context to preserve across sessions.

How: Use `listDailyContext` to read recent entries (view="Grid view"). Use Entry Timestamp for timing, Logged At for recency. Create new entry with `createDailyContext` for mood/energy/events. Use most recent entry if multiple apply.

Fields:

- Logged At: Formula (DATETIME_FORMAT(SET_TIMEZONE(CREATED_TIME(), 'America/Los_Angeles'), 'LLLL')) – when entry was submitted.

- Entry Timestamp: User-specified time (UTC ISO 8601, e.g., "6 PM tomorrow" → 2025-08-12T01:00:00Z).

- Mental/Physical Energy Available, Focus Level: extra low, low, medium, high, extra high.

- Mood, Availability, Weather, Notes: Freeform text (e.g., "Hot outside, prefer indoor tasks" in Weather/Notes).



Repeating Tasks

On completion: Update `Last Done` (UTC ISO 8601), set Status to "done." Automation handles future cycles. Check for follow-ups or dependent tasks.



Record ID

Use Record ID field to RECORD_ID() for accurate IDs. Verify ID before updates. If update fails, recheck ID.



Task Creation

If user refers to a task, don't store it in memory—check Airtable for a matching task, then update with provided info. If no match, create a new task. Never suggest tasks not in Airtable unless tied to existing ones."""

client = Anthropic(api_key=ANTHROPIC_API_KEY, http_client=None)

tools = [
    {
        "name": "list_current_tasks",
        "description": "List tasks from Airtable 'Current tasks only' view.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "create_task",
        "description": "Create a new task in Airtable.",
        "input_schema": {
            "type": "object",
            "properties": {"fields": {"type": "object"}},
            "required": ["fields"]
        }
    },
    {
        "name": "update_task",
        "description": "Update an existing task in Airtable by Record ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "fields": {"type": "object"}
            },
            "required": ["record_id", "fields"]
        }
    },
    {
        "name": "createDailyContext",
        "description": "Create a new entry in Airtable Daily Context table for mood, energy, or events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "properties": {
                        "Entry Timestamp": {"type": "string"},
                        "Mood": {"type": "string"},
                        "Availability": {"type": "string"},
                        "Weather": {"type": "string"},
                        "Notes": {"type": "string"},
                        "Mental Energy Available": {"type": "string"},
                        "Physical Energy Available": {"type": "string"},
                        "Focus Level": {"type": "string"}
                    },
                    "required": ["Entry Timestamp"]
                }
            },
            "required": ["fields"]
        }
    }
]

def call_airtable(endpoint, method="GET", data=None, params=None):
    url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{endpoint}"
    headers = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}
    if method == "GET":
        return requests.get(url, headers=headers, params=params).json()
    elif method == "POST":
        return requests.post(url, headers=headers, json=data).json()
    elif method == "PATCH":
        return requests.patch(url, headers=headers, json=data).json()

def execute_tool(tool_name, tool_input):
    if tool_name == "list_current_tasks":
        return call_airtable("GPT%20master%20list", params={"view": "Current tasks only"})
    elif tool_name == "create_task":
        return call_airtable("GPT%20master%20list", method="POST", data={"fields": tool_input["fields"]})
    elif tool_name == "update_task":
        record_id = tool_input["record_id"]
        return call_airtable(f"GPT%20master%20list/{record_id}", method="PATCH", data={"fields": tool_input["fields"]})
    elif tool_name == "createDailyContext":
        return call_airtable("Daily%20Context", method="POST", data={"fields": tool_input["fields"]})
    raise ValueError(f"Unknown tool: {tool_name}")

@app.get("/")
async def health_check():
    return {"status": "healthy"}

@app.post("/")
async def bot(request: Request):
    json_body = await request.json()
    logger.info(f"Received request body: {json_body}")

    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header.split(" ")[1] != POE_SERVER_KEY:
        logger.error("Unauthorized request")
        raise HTTPException(status_code=401, detail="Unauthorized")

    if json_body.get("type") == "settings":
        return {
            "model": "claude-3-5-haiku-20241022",
            "tools": tools
        }

    elif json_body.get("type") == "query":
        try:
            messages = json_body.get("query", [])
            claude_messages = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in messages if msg["role"] in ["user", "assistant"]
            ]
            claude_messages.insert(0, {"role": "user", "content": SYSTEM_PROMPT})

            response = client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=1024,
                messages=claude_messages,
                tools=tools
            )
            logger.info(f"Claude response: stop_reason={response.stop_reason}, content={response.content}")

            output = "Sorry, I couldn't understand the tool request."

            if response.stop_reason == "tool_use" and hasattr(response.content[-1], "name"):
                tool_use = response.content[-1]
                tool_name = tool_use.name
                tool_input = tool_use.input
                tool_result = execute_tool(tool_name, tool_input)

                claude_messages.append({"role": "assistant", "content": response.content})
                claude_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": json.dumps(tool_result)
                    }]
                })

                final_response = client.messages.create(
                    model="claude-3-5-haiku-20241022",
                    max_tokens=1024,
                    messages=claude_messages,
                    tools=tools
                )
                logger.info(f"Final Claude response: stop_reason={final_response.stop_reason}, content={final_response.content}")

                if final_response.content and hasattr(final_response.content[0], "text"):
                    output = final_response.content[0].text
                else:
                    output = "Sorry, I didn't get a final response from Claude."
            elif response.stop_reason == "end_turn" and response.content:
                # Handle direct responses without tool use
                if hasattr(response.content[0], "text"):
                    output = response.content[0].text

            logger.info(f"Sending response to Poe: {output}")

            # FIXED: Use correct Poe event types - "text" not "message"
            # Build SSE response manually with correct event types
            sse_content = f"event: text\ndata: {json.dumps({'text': output})}\n\nevent: done\ndata: {{}}\n\n"
            logger.info(f"SSE content: {repr(sse_content)}")
            
            return Response(
                content=sse_content,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive"
                }
            )

        except Exception as e:
            logger.error(f"Error during query handling: {e}")
            
            # Error response with correct Poe event types
            error_sse = f"event: error\ndata: {json.dumps({'text': 'Oops! Something went wrong while processing your request.'})}\n\nevent: done\ndata: {{}}\n\n"
            
            return Response(
                content=error_sse,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive"
                }
            )

    # Handle other request types (like report_error)
    elif json_body.get("type") == "report_error":
        logger.info(f"Received error report: {json_body.get('message', 'No message')}")
        return {"status": "acknowledged"}

    # Default response for unknown request types
    return {"status": "unknown_request_type"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
