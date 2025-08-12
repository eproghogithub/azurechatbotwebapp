

# Bot Framework SDK (Python) + Azure AI Language Question Answering
# Local run: python app.py  → test with Bot Framework Emulator at http://localhost:3978/api/messages

from aiohttp import web
import os, sys, time, json, logging
from botbuilder.core import ActivityHandler, TurnContext, BotFrameworkAdapter, BotFrameworkAdapterSettings
from botbuilder.schema import Activity
from azure.core.credentials import AzureKeyCredential
from azure.ai.language.questionanswering import QuestionAnsweringClient

# ======== CONFIG: replace with your values (NO environment variables) ========
APP_ID  = "5b071f37-6086-461a-a8b7-2a56daeeb32a"   # e.g., "00000000-0000-0000-0000-000000000000" (leave "" for local Emulator without auth)
APP_PW  = "73a33cde-5f89-4b8f-a4c1-33231bc7a21f"   # client secret (leave "" for local Emulator without auth)

AZURE_LANGUAGE_ENDPOINT = "https://prosenjitnlp1.cognitiveservices.azure.com".rstrip("/")
AZURE_LANGUAGE_KEY      = "E8JBjaa05yTzIW2XmkfaFmNF92J7Aw7YZlyD1ISLvy6YLeLgsau7JQQJ99BHACYeBjFXJ3w3AAAaACOGpEXu"

AZURE_QNA_PROJECT       = "my-faq-project"
AZURE_QNA_DEPLOYMENT    = "production"
QNA_CONF_THRESHOLD      = 0.50

PORT = 8000  # App Service will override this with its own PORT; locally 3978 is standard
# ============================================================================
# ---------- Logging setup ----------
LOG_LEVEL = "DEBUG"  # change to "DEBUG" for deeper traces
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)
log = logging.getLogger("bot")
# Optional: turn up/down Azure SDK logs
logging.getLogger("azure").setLevel(logging.WARNING)   # set to INFO/DEBUG if needed
logging.getLogger("aiohttp.access").setLevel(logging.INFO)

def _mask(s: str, show=4):
    if not s: return ""
    return s[:show] + "…" if len(s) > show else s

log.info(
    "Startup config | endpoint=%s project=%s deployment=%s threshold=%.2f appId=%s",
    AZURE_LANGUAGE_ENDPOINT, AZURE_QNA_PROJECT, AZURE_QNA_DEPLOYMENT, QNA_CONF_THRESHOLD, _mask(APP_ID)
)

# ---------- AIOHTTP middleware: log every HTTP request ----------
@web.middleware
async def request_logger(request, handler):
    t0 = time.time()
    try:
        response = await handler(request)
        dt = (time.time() - t0) * 1000
        log.info("HTTP %s %s -> %s (%.1f ms)", request.method, request.path_qs, response.status, dt)
        return response
    except Exception:
        dt = (time.time() - t0) * 1000
        log.exception("HTTP %s %s FAILED (%.1f ms)", request.method, request.path_qs, dt)
        raise

# ---------- QnA client ----------
qa_client = QuestionAnsweringClient(
    AZURE_LANGUAGE_ENDPOINT,
    AzureKeyCredential(AZURE_LANGUAGE_KEY)
)

# ---------- Bot ----------
class QnABot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        text = (turn_context.activity.text or "").strip()
        chan = getattr(turn_context.activity, "channel_id", "unknown")
        conv = getattr(turn_context.activity, "conversation", None)
        conv_id = getattr(conv, "id", None) if conv else None

        log.info("Activity: channel=%s convId=%s text=%r", chan, conv_id, text)

        if not text:
            await turn_context.send_activity("Say something and I’ll try to help!")
            return

        try:
            t0 = time.time()
            resp = qa_client.get_answers(
                question=text,
                project_name=AZURE_QNA_PROJECT,
                deployment_name=AZURE_QNA_DEPLOYMENT
            )
            dt = (time.time() - t0) * 1000
            n = len(resp.answers or [])
            log.info("QnA returned %d answer(s) in %.1f ms", n, dt)

            if not resp.answers:
                log.info("QnA: no answers → using fallback")
                await turn_context.send_activity("Sorry, I don’t know the answer.")
                return

            best = resp.answers[0]
            ans_txt = (best.answer or "").replace("\n", " ").strip()
            conf = float(best.confidence or 0.0)
            src = getattr(best, "source", None)

            log.info("QnA BEST | conf=%.2f source=%s answer=%r", conf, src, ans_txt[:200])

            if conf < QNA_CONF_THRESHOLD:
                log.info("QnA: confidence %.2f < %.2f → fallback", conf, QNA_CONF_THRESHOLD)
                await turn_context.send_activity("Sorry, I don’t know the answer.")
                return

            await turn_context.send_activity(ans_txt)

        except Exception:
            log.exception("QnA call failed")
            await turn_context.send_activity("Sorry, something went wrong.")

# ---------- Adapter & routes ----------
adapter = BotFrameworkAdapter(BotFrameworkAdapterSettings(APP_ID, APP_PW))
bot = QnABot()

async def messages(req: web.Request) -> web.Response:
    if "application/json" not in (req.headers.get("Content-Type") or ""):
        log.warning("Bad Content-Type: %s", req.headers.get("Content-Type"))
        return web.Response(status=415, text="Content-Type must be application/json")

    body = await req.json()
    # log incoming activity summary (truncate safely)
    try:
        snippet = json.dumps(
            {k: body.get(k) for k in ("type", "text", "channelId", "deliveryMode")},
            ensure_ascii=False
        )
        log.info("Incoming activity: %s", snippet)
    except Exception:
        log.debug("Raw activity: %s", body)

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    try:
        await adapter.process_activity(activity, auth_header, bot.on_turn)
        # If client used deliveryMode=expectReplies, SDK returns activities inline
        return web.Response(status=201, text="OK")
    except Exception:
        log.exception("process_activity failed")
        return web.Response(status=500, text="process_activity failed")

def create_app():
    app = web.Application(middlewares=[request_logger])
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/", lambda req: web.Response(text="Bot is running."))  # health
    return app

if __name__ == "__main__":
    PORT = int(os.getenv("PORT", "8000"))  # 8000 locally; Azure injects PORT value
    log.info("Listening on 0.0.0.0:%s", PORT)
    web.run_app(create_app(), host="0.0.0.0", port=PORT)