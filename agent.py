from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.responses import StreamingResponse
import os
import httpx
import base64

# ── Load .env ─────────────────────────────────────────────
load_dotenv()

client = OpenAI(api_key=os.getenv("OpenAIAPIKEY"))

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

GITHUB_HEADERS = {"Accept": "application/vnd.github+json"}

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
    {
        "type": "function",
        "name": "get_file_tree",
        "description": "Get all file paths in the repo.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a file from repo.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string"
                }
            },
            "required": ["path"]
        }
    },
    {
        "type": "function",
        "name": "search_code",
        "description": "Search code in repo.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string"
                }
            },
            "required": ["query"]
        }
    }
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
        messages = req.history + [
            {"role": "user", "content": req.query}
        ]

        yield "data: 🧠 Starting analysis...\n\n"

        for step in range(8):
            yield f"data: 🔍 Thinking step {step + 1}/8...\n\n"

            response = client.responses.create(
                model="gpt-4.1-mini",
                input=messages,
                tools=TOOLS,
            )

            if response.output_text:
                yield f"data: ✅ Done\n\n"
                yield f"data: {response.output_text}\n\n"
                return

            for item in response.output:
                messages.append(item)

            for item in response.output:
                if item.type == "function_call":

                    if item.name == "get_file_tree":
                        yield "data: 📂 Looking through repository files...\n\n"

                    elif item.name == "read_file":
                        yield f"data: 📄 Reading {json.loads(item.arguments)['path']}...\n\n"

                    elif item.name == "search_code":
                        yield f"data: 🔎 Searching code...\n\n"

                    args = json.loads(item.arguments)

                    result = await execute_tool(
                        item.name,
                        args,
                        owner,
                        repo
                    )

                    messages.append({
                        "type": "function_call_output",
                        "call_id": item.call_id,
                        "output": result
                    })

            await asyncio.sleep(0.2)

        yield "data: ❌ Step limit reached\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream"
    )
