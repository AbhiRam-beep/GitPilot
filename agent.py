from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from fastapi.responses import StreamingResponse
import os
import httpx
import base64
import asyncio
load_dotenv()
from google import genai
from google.genai import types
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://github.com",           # origin seen on content script fetches
        "chrome-extension://fjmeakdoilpaalfhabjhknoodddiibce"  # if you ever call from popup/background
    ],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)
# ── GitHub helpers ────────────────────────────────────────

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {os.getenv('GITHUB_TOKEN')}"
}

async def get_file_tree(owner: str, repo: str) -> list[str]:
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
    async with httpx.AsyncClient() as http:
        data = (await http.get(url, headers=GITHUB_HEADERS)).json()

    noise = ["node_modules/", "dist/", "build/", ".lock",
             ".png", ".jpg", ".svg", ".ico", ".woff"]

    return [
        node["path"]
        for node in data.get("tree", [])
        if node["type"] == "blob"
        and not any(n in node["path"] for n in noise)
    ]


async def read_file(owner: str, repo: str, path: str) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    async with httpx.AsyncClient() as http:
        data = (await http.get(url, headers=GITHUB_HEADERS)).json()

    if "content" not in data:
        return f"Error: could not read {path}"

    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


async def search_code(owner: str, repo: str, query: str) -> list[str]:
    url = f"https://api.github.com/search/code?q={query}+repo:{owner}/{repo}"
    async with httpx.AsyncClient() as http:
        data = (await http.get(url, headers=GITHUB_HEADERS)).json()

    return [item["path"] for item in data.get("items", [])][:10]


# ── Tools schema (OpenAI format) ──────────────────────────

TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="get_file_tree",
            description="Get all file paths in the repo.",
            parameters=types.Schema(type="OBJECT", properties={})
        ),
        types.FunctionDeclaration(
            name="read_file",
            description="Read a file from repo.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"path": types.Schema(type="STRING")},
                required=["path"]
            )
        ),
        types.FunctionDeclaration(
            name="search_code",
            description="Search code in repo.",
            parameters=types.Schema(
                type="OBJECT",
                properties={"query": types.Schema(type="STRING")},
                required=["query"]
            )
        ),
    ])
]


# ── Request model ─────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    repo: str
    history: list = []


# ── Tool executor ─────────────────────────────────────────

async def execute_tool(name: str, args: dict, owner: str, repo: str):
    if name == "get_file_tree":
        return "\n".join(await get_file_tree(owner, repo))

    if name == "read_file":
        return await read_file(owner, repo, args["path"])

    if name == "search_code":
        return "\n".join(await search_code(owner, repo, args["query"]))

    return "Unknown tool"


# ── Chat endpoint ─────────────────────────────────────────

@app.post("/chat")
async def handlechat(req: ChatRequest):
    owner, repo = req.repo.split("/", 1)

    
    async def event_stream():
        chat = client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                tools=TOOLS,
                system_instruction="""You are a code analysis agent for GitHub repositories.
    When asked about code, ALWAYS start by calling get_file_tree to see all files.
    Then call read_file on any files that seem relevant to the question.
    Only use search_code as a last resort, as it is unreliable.
    Never say something doesn't exist without first reading the relevant files yourself."""
            )
        )

        history_text = ""
        for msg in req.history:
            history_text += f"{msg['role']}: {msg['content']}\n"

        first_message = (history_text + f"user: {req.query}") if history_text else req.query

        yield "data: 🧠 Starting analysis...\n\n"

        # Send the first message
        response = chat.send_message(first_message)

        for step in range(8):
            yield f"data: 🔍 Thinking step {step + 1}/8...\n\n"

            # Check for text parts explicitly (avoid the warning)
            text_parts = [p.text for p in response.candidates[0].content.parts if p.text]
            function_calls = [p.function_call for p in response.candidates[0].content.parts if p.function_call]

            if not function_calls:
                # No tool calls — this is the final answer
                final = "\n".join(text_parts) if text_parts else "No response generated."
                yield f"data: ✅ Done\n\n"
                safe = final.replace("\n", "<br>")
                yield f"data: {safe}\n\n"
                return

            # Execute all tool calls
            tool_results = []
            for fn in function_calls:
                name = fn.name
                args = dict(fn.args)

                if name == "get_file_tree":
                    yield "data: 📂 Looking through repository files...\n\n"
                elif name == "read_file":
                    yield f"data: 📄 Reading {args.get('path', '')}...\n\n"
                elif name == "search_code":
                    yield "data: 🔎 Searching code...\n\n"

                result = await execute_tool(name, args, owner, repo)
                tool_results.append(
                    types.Part.from_function_response(name=name, response={"result": result})
                )

            # Send tool results and get next response
            response = chat.send_message(tool_results)
            await asyncio.sleep(0.1)

        yield "data: ❌ Step limit reached\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
