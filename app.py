

# Bot Framework SDK (Python) + Azure AI Language Question Answering
# Local run: python app.py  → test with Bot Framework Emulator at http://localhost:3978/api/messages

from aiohttp import web
from pathlib import Path
import os, sys, time, json, logging
from botbuilder.core import ActivityHandler, TurnContext, BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity
from azure.core.credentials import AzureKeyCredential
from azure.ai.language.questionanswering import QuestionAnsweringClient

# ======== CONFIG: replace with your values (NO environment variables) ========
APP_ID  = "60c9d0f0-bbb0-4194-9ed2-627e87016387"   # e.g., "00000000-0000-0000-0000-000000000000" (leave "" for local Emulator without auth)
APP_PW  = "ddu8Q~A0b9iHsNU.HPyAtqyRkLUIKsKkk~mmubxD"   # client secret (leave "" for local Emulator without auth)

AZURE_LANGUAGE_ENDPOINT = "https://prosenjitnlp1.cognitiveservices.azure.com".rstrip("/")
AZURE_LANGUAGE_KEY      = "E8JBjaa05yTzIW2XmkfaFmNF92J7Aw7YZlyD1ISLvy6YLeLgsau7JQQJ99BHACYeBjFXJ3w3AAAaACOGpEXu"

AZURE_QNA_PROJECT       = "my-faq-project"
AZURE_QNA_DEPLOYMENT    = "production"
QNA_CONF_THRESHOLD      = 0.50

PORT = 8000  # App Service will override this with its own PORT; locally 3978 is standard
# ============================================================================
# ---------- traffic.log in current directory ----------
BASE_DIR = Path(__file__).resolve().parent
TRAFFIC_LOG = BASE_DIR / "traffic.log"

def _init_traffic_log():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRAFFIC_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "startup",
            "base_dir": str(BASE_DIR),
            "log_path": str(TRAFFIC_LOG)
        }, ensure_ascii=False) + "\n")
    print(f"[traffic-log] Using {TRAFFIC_LOG}", file=sys.stdout)

_init_traffic_log()

def write_traffic(record: dict):
    with open(TRAFFIC_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

# ---------- console logging ----------
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
log = logging.getLogger("bot")

# Optional: show connector auth details if needed
# logging.getLogger("botframework.connector.auth").setLevel(logging.DEBUG)

# ---------- Startup audit (prove which values are used) ----------
def _mask(s, n=6): return s[:n]+"…" if s else "<empty>"
print(
    "[startup] file=%s  appId=%s  secret_len=%s  qna=%s/%s  endpoint=%s" %
    (__file__, _mask(APP_ID), len(APP_PW or ""), AZURE_QNA_PROJECT, AZURE_QNA_DEPLOYMENT, AZURE_LANGUAGE_ENDPOINT),
    flush=True
)

# ---------- HTTP traffic logger middleware ----------
@web.middleware
async def traffic_http_logger(request, handler):
    t0 = time.time()
    req_headers = {k: v for k, v in request.headers.items()
                   if k.lower() in ("content-type", "user-agent", "authorization")}
    if "authorization" in req_headers:
        req_headers["authorization"] = "<masked>"

    raw_body = None
    if request.can_read_body:
        try:
            raw_body = await request.text()
        except Exception:
            raw_body = None

    try:
        response = await handler(request)
        elapsed = int((time.time() - t0) * 1000)
        resp_text = getattr(response, "text", None)
        if callable(resp_text):  # aiohttp may expose as callable in some versions
            resp_text = None
        write_traffic({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "http_traffic",
            "http": {
                "remote": request.remote,
                "method": request.method,
                "path": request.path,
                "query": request.query_string,
                "headers": req_headers,
                "body_preview": (raw_body[:2000] if raw_body else None),
                "status": response.status,
                "resp_content_type": getattr(response, "content_type", None),
                "resp_length": getattr(response, "content_length", None),
                "resp_text_preview": (resp_text[:1000] if isinstance(resp_text, str) else None),
                "elapsed_ms": elapsed
            }
        })
        return response
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        write_traffic({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "http_traffic_error",
            "http": {
                "remote": request.remote,
                "method": request.method,
                "path": request.path,
                "query": request.query_string,
                "headers": req_headers,
                "body_preview": (raw_body[:2000] if raw_body else None),
                "status": 500,
                "elapsed_ms": elapsed
            },
            "error": f"{type(e).__name__}: {e}"
        })
        raise

# ---------- Azure QnA client ----------
qa_client = QuestionAnsweringClient(
    AZURE_LANGUAGE_ENDPOINT,
    AzureKeyCredential(AZURE_LANGUAGE_KEY)
)

# ---------- Bot logic ----------
class QnABot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        act = turn_context.activity
        user_text = (act.text or "").strip()

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "turn",
            "request": {
                "text": user_text,
                "channelId": getattr(act, "channel_id", None),
                "conversationId": getattr(getattr(act, "conversation", None), "id", None),
            },
            "qna": None,
            "response": None,
        }

        if not user_text:
            reply = "Say something and I’ll try to help!"
            record["response"] = {"text": reply, "fallback": True}
            write_traffic(record)
            await turn_context.send_activity(reply)
            return

        try:
            t0 = time.time()
            resp = qa_client.get_answers(
                question=user_text,
                project_name=AZURE_QNA_PROJECT,
                deployment_name=AZURE_QNA_DEPLOYMENT
            )
            ms = int((time.time() - t0) * 1000)

            answers = resp.answers or []
            best = answers[0] if answers else None
            conf = float(getattr(best, "confidence", 0.0) or 0.0) if best else 0.0
            ans  = (best.answer or "").replace("\n", " ").strip() if best else ""
            src  = getattr(best, "source", None) if best else None

            record["qna"] = {
                "count": len(answers),
                "elapsed_ms": ms,
                "best_confidence": conf,
                "best_source": src,
                "best_preview": ans[:200]
            }

            if not best or conf < QNA_CONF_THRESHOLD:
                reply = "Sorry, I don’t know the answer."
                record["response"] = {"text": reply, "fallback": True}
                write_traffic(record)
                await turn_context.send_activity(reply)
                return

            reply = ans
            record["response"] = {"text": reply, "fallback": False, "confidence": conf, "source": src}
            write_traffic(record)
            await turn_context.send_activity(reply)

        except Exception as e:
            reply = "Sorry, something went wrong."
            record["error"] = f"{type(e).__name__}: {e}"
            record["response"] = {"text": reply, "fallback": True}
            write_traffic(record)
            log.exception("QnA call failed")
            await turn_context.send_activity(reply)

# ---------- Adapter & routes ----------
# Explicit OAuth scope so it matches your successful token test
settings = BotFrameworkAdapterSettings(APP_ID, APP_PW)
settings.oauth_scope = "https://api.botframework.com/.default"
adapter = BotFrameworkAdapter(settings)

bot = QnABot()

async def messages(req: web.Request) -> web.Response:
    if "application/json" not in (req.headers.get("Content-Type") or ""):
        write_traffic({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": "bad_content_type",
            "path": "/api/messages",
            "content_type": req.headers.get("Content-Type")
        })
        return web.Response(status=415, text="Content-Type must be application/json")

    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    await adapter.process_activity(activity, auth_header, bot.on_turn)

    resp = web.Response(status=201, text="OK")
    write_traffic({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "route_response",
        "path": "/api/messages",
        "status": resp.status,
        "text": resp.text
    })
    return resp

def create_app():
    app = web.Application(middlewares=[traffic_http_logger])
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/", lambda req: web.Response(text="Bot is running."))  # health
    return app

if __name__ == "__main__":
    PORT = int(os.getenv("PORT", "8000"))  # Azure sets PORT; 8000 for local
    print(f"Listening on 0.0.0.0:{PORT}  (traffic log: {TRAFFIC_LOG})")
    web.run_app(create_app(), host="0.0.0.0", port=PORT)