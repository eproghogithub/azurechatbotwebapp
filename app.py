

# Bot Framework SDK (Python) + Azure AI Language Question Answering
# Local run: python app.py  → test with Bot Framework Emulator at http://localhost:3978/api/messages
import os
from aiohttp import web
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

# Create a single reusable client
qa_client = QuestionAnsweringClient(
    AZURE_LANGUAGE_ENDPOINT,
    AzureKeyCredential(AZURE_LANGUAGE_KEY)
)

class QnABot(ActivityHandler):
    async def on_message_activity(self, turn_context: TurnContext):
        text = (turn_context.activity.text or "").strip()
        if not text:
            await turn_context.send_activity("Say something and I’ll try to help!")
            return
        try:
            # Call Question Answering
            resp = qa_client.get_answers(
                question=text,
                project_name=AZURE_QNA_PROJECT,
                deployment_name=AZURE_QNA_DEPLOYMENT
            )

            # Fallback if no answers or low confidence
            if not resp.answers:
                await turn_context.send_activity("Sorry, I don’t know the answer.")
                return
            best = resp.answers[0]
            if best.confidence is None or best.confidence < QNA_CONF_THRESHOLD:
                await turn_context.send_activity("Sorry, I don’t know the answer.")
                return

            await turn_context.send_activity(best.answer)

        except Exception as e:
            # Log server-side; keep user message friendly
            print("[QnA error]", e)
            await turn_context.send_activity("Sorry, something went wrong.")

# Bot Framework adapter & HTTP wiring
adapter = BotFrameworkAdapter(BotFrameworkAdapterSettings(APP_ID, APP_PW))
bot = QnABot()

async def messages(req: web.Request) -> web.Response:
    if "application/json" not in req.headers.get("Content-Type", ""):
        return web.Response(status=415, text="Content-Type must be application/json")
    body = await req.json()
    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")
    await adapter.process_activity(activity, auth_header, bot.on_turn)
    return web.Response(status=201)

def create_app():
    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/", lambda req: web.Response(text="Bot is running."))  # simple health check
    return app

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
