"""Local FormLab sandbox server. Runs are saved only under this project."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RUNS_FILE = DATA_DIR / "test-runs.json"
JOBS_FILE = DATA_DIR / "submission-jobs.json"
ENV_FILE = ROOT / ".env"
TOKEN_FILE = DATA_DIR / "google-token.json"
OAUTH_STATE = None
SCOPE = "https://www.googleapis.com/auth/forms.body.readonly"
JOBS = {}
JOBS_LOCK = threading.Lock()
LIVE_TEST_FORM_SOURCE_ID = "1g7R4cg0AmLP5g9WC7gqV1LhrdqzphNPEdvn0tZCHK-s" #"14oo20i70apGimbUCGsN5NXNe47gUHV7WTtDScoLxkko"
LIVE_TEST_FORM_RESPONDER_ID = "1FAIpQLSfyoSQDP5gJdUbyJFvaZ2O3aNnlWT6zlBRk012F0ZO4KKaTkA" #"1FAIpQLSdu1caOafkcvVlgqR9zA9xr9C5ToPAyX9g_K0UtyWzCNvV6qQ"


def load_dotenv():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        os.environ.setdefault(key.strip(), value)


load_dotenv()


def oauth_config():
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Google sign-in is not configured. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env, then restart run.bat.")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "project_id": os.environ.get("GOOGLE_PROJECT_ID", "").strip(),
        "auth_uri": os.environ.get("GOOGLE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth").strip(),
        "token_uri": os.environ.get("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token").strip(),
        "auth_provider_x509_cert_url": os.environ.get("GOOGLE_AUTH_PROVIDER_X509_CERT_URL", "").strip(),
        "redirect_uri": os.environ.get("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/auth/callback/google").strip(),
    }


def save_token(token):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token, indent=2), encoding="utf-8")


def load_token():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def post_google_form(url, fields):
    body = urlencode(fields).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def google_api_error(exc):
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        error = json.loads(raw).get("error", {})
    except (json.JSONDecodeError, AttributeError):
        error = {}
    message = str(error.get("message", "")).strip()
    reasons = {str(item.get("reason", "")).lower() for item in error.get("errors", []) if isinstance(item, dict)}
    status = str(error.get("status", "")).lower()
    if reasons.intersection({"accessnotconfigured", "servicedisabled", "service_disabled"}):
        return "Google Forms API is disabled for this Google Cloud project. Enable Google Forms API in that project, wait a few minutes, then retry."
    if reasons.intersection({"insufficientpermissions", "access_token_scope_insufficient"}) or status == "permission_denied":
        return "Google denied this form request. Confirm the connected account has Viewer or Editor access to this form and reconnect if the approval did not include forms.body.readonly. " + message
    if exc.code == 403:
        return "Google returned 403 Forbidden. Check that Google Forms API is enabled in the OAuth client's project and that the connected account can open this form. " + message
    return f"Google Forms returned HTTP {exc.code}. " + (message or "Check the form link and Google Cloud API settings.")


def access_token():
    token = load_token()
    if not token:
        raise PermissionError("Connect your Google account first.")
    if token.get("access_token") and token.get("expires_at", 0) > time.time() + 60:
        return token["access_token"]
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise PermissionError("Google sign-in expired. Connect your Google account again.")
    config = oauth_config()
    result = post_google_form(config.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "client_id": config["client_id"], "client_secret": config["client_secret"],
        "refresh_token": refresh_token, "grant_type": "refresh_token"
    })
    token["access_token"] = result["access_token"]
    token["expires_at"] = time.time() + int(result.get("expires_in", 3600))
    save_token(token)
    return token["access_token"]


def resolve_form_id(raw_url):
    parsed = urlparse(raw_url)
    if parsed.scheme != "https" or parsed.hostname not in ("docs.google.com", "forms.gle"):
        raise ValueError("Use an https://docs.google.com/forms/... or https://forms.gle/... link.")
    if parsed.hostname == "forms.gle":
        req = Request(raw_url, headers={"User-Agent": "FormLabSandbox/1.0"})
        with urlopen(req, timeout=15) as response:
            parsed = urlparse(response.geturl())
        if parsed.hostname != "docs.google.com":
            raise ValueError("That forms.gle link did not redirect to a Google Form.")
    parts = [p for p in parsed.path.split("/") if p]
    try:
        marker = parts.index("d")
        form_id = parts[marker + 1]
        if form_id == "e":
            form_id = parts[marker + 2]
    except (ValueError, IndexError):
        raise ValueError("Could not find a form ID in that link. Paste the Google Forms share or edit link.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", form_id):
        raise ValueError("The form ID in that URL is not valid.")
    return form_id


def read_form(raw_url):
    form_id = resolve_form_id(raw_url)
    token = access_token()
    api_url = "https://forms.googleapis.com/v1/forms/" + form_id
    req = Request(api_url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError(google_api_error(exc))
        if exc.code == 404:
            raise ValueError("Google Forms could not find that form ID. Try the form's editor link, ending in /edit.")
        raise RuntimeError(google_api_error(exc)) from exc
    questions = []
    unsupported_required = []
    for item in payload.get("items", []):
        q = item.get("questionItem", {}).get("question", {})
        title = item.get("title", "Untitled question")

        def add_choice_question(question, question_title, choice, question_type, group_title="", row_title=""):
            options = [str(option.get("value", "")).strip() for option in choice if str(option.get("value", "")).strip()]
            if not options:
                return
            checkbox = question_type in ("Checkbox", "Checkbox grid")
            answers = [{"label": option, "value": 50 if checkbox else 0} for option in options]
            questions.append({"title": question_title, "type": question_type,
                              "required": bool(question.get("required")), "answers": answers,
                              "groupTitle": group_title, "rowTitle": row_title})

        if item.get("questionItem"):
            choice = q.get("choiceQuestion", {})
            kind = choice.get("type")
            if kind in ("RADIO", "DROP_DOWN", "CHECKBOX"):
                add_choice_question(q, title, choice.get("options", []),
                                    {"RADIO": "Multiple choice", "DROP_DOWN": "Dropdown", "CHECKBOX": "Checkbox"}[kind])
            elif q.get("scaleQuestion"):
                scale = q["scaleQuestion"]
                low, high = int(scale.get("low", 1)), int(scale.get("high", 5))
                answers = [{"label": str(n), "value": 0} for n in range(low, high + 1)]
                if answers:
                    questions.append({"title": title, "type": "Scale", "required": bool(q.get("required")),
                                      "answers": answers, "lowLabel": scale.get("lowLabel", ""), "highLabel": scale.get("highLabel", "")})
            elif q.get("ratingQuestion"):
                rating = q["ratingQuestion"]
                answers = [{"label": str(n), "value": 0} for n in range(1, int(rating.get("ratingScaleLevel", 5)) + 1)]
                if answers:
                    questions.append({"title": title, "type": "Rating", "required": bool(q.get("required")), "answers": answers,
                                      "iconType": rating.get("iconType", "STAR")})
            elif q.get("required"):
                unsupported_required.append(title)
        elif item.get("questionGroupItem"):
            group = item["questionGroupItem"]
            grid = group.get("grid", {})
            kind = grid.get("columns", {}).get("type")
            if kind in ("RADIO", "CHECKBOX", "CHECK_BOX"):
                row_type = "Radio grid" if kind == "RADIO" else "Checkbox grid"
                for row in group.get("questions", []):
                    row_info = row.get("rowQuestion", {})
                    row_title = row_info.get("title", "")
                    add_choice_question(row, f"{title}: {row_title}", grid.get("columns", {}).get("options", []),
                                        row_type, title, row_title)
            else:
                unsupported_required.append(title)
    if not questions:
        raise ValueError("No supported choice, scale, rating, or grid questions were found. Free-text, date, time, and file-upload questions are not supported by the percentage runner.")
    if unsupported_required:
        names = ", ".join(unsupported_required[:3])
        raise ValueError(f"These required questions are not choice-based and cannot use percentage responses: {names}. Free-text, date, time, and file-upload questions are unsupported.")
    return {"title": payload.get("info", {}).get("title", "Google Form"),
            "responderUri": payload.get("responderUri", ""), "questions": questions}


def persist_jobs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with JOBS_LOCK:
        JOBS_FILE.write_text(json.dumps(list(JOBS.values())[:30], ensure_ascii=False, indent=2), encoding="utf-8")


def add_job_log(job_id, message, level=""):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["logs"].append({"time": datetime.now().strftime("%H:%M:%S"), "message": message, "level": level})
        JOBS_FILE.write_text(json.dumps(list(JOBS.values())[:30], ensure_ascii=False, indent=2), encoding="utf-8")


def update_job(job_id, **values):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)
            JOBS_FILE.write_text(json.dumps(list(JOBS.values())[:30], ensure_ascii=False, indent=2), encoding="utf-8")


def visible_question_card(page, title):
    matches = page.locator('[role="listitem"]').filter(has_text=title)
    for index in range(matches.count()):
        candidate = matches.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def submit_responses(job_id, responder_uri, title, questions, responses):
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
    except ImportError:
        message = "Browser automation is not installed. Run setup-browser.bat, then restart run.bat."
        add_job_log(job_id, message, "error")
        update_job(job_id, status="error", error=message, current="Browser automation unavailable")
        return

    update_job(job_id, status="running", current="Starting browser automation")
    add_job_log(job_id, f"Starting {len(responses)} test submissions for {title}.")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            # Google Forms renders long custom dropdown menus in the page viewport;
            # a taller viewport keeps lower options reachable by Playwright.
            page = browser.new_page(viewport={"width": 1280, "height": 5000})
            try:
                for response_index, response in enumerate(responses, start=1):
                    submit_clicked = False
                    try:
                        update_job(job_id, current=f"Opening form for submission {response_index} of {len(responses)}")
                        page.goto(responder_uri, wait_until="domcontentloaded", timeout=30000)
                        add_job_log(job_id, f"Opened the responder page for submission {response_index}.")
                        for question_index, question in enumerate(questions):
                            answer = response["answers"][question_index]
                            question_locator_title = question.get("groupTitle") or question["title"]
                            card = visible_question_card(page, question_locator_title)
                            for _ in range(10):
                                if card is not None:
                                    break
                                next_button = page.get_by_role("button", name=re.compile(r"^next$", re.I))
                                if not next_button.count():
                                    break
                                next_button.click()
                                try:
                                    page.get_by_role("listitem").filter(has_text=question_locator_title).first.wait_for(state="visible", timeout=6000)
                                except PlaywrightTimeoutError:
                                    page.wait_for_timeout(400)
                                card = visible_question_card(page, question_locator_title)
                            if card is None:
                                raise RuntimeError(f"Could not find question: {question['title']}. Check the form's page order, required access, or question type.")
                            update_job(job_id, current=f"Filling submission {response_index}: {question['title']}")
                            target = card
                            if question.get("groupTitle"):
                                roles = ("group", "radiogroup", "row")
                                row_target = None
                                for role in roles:
                                    candidate = card.get_by_role(role).filter(has_text=question.get("rowTitle", ""))
                                    if candidate.count():
                                        row_target = candidate.last
                                        break
                                if row_target is None:
                                    row_target = card.get_by_text(question.get("rowTitle", ""), exact=True).locator("xpath=..").locator("xpath=..").first
                                target = row_target
                            if question.get("type") in ("Checkbox", "Checkbox grid"):
                                for selected_answer in answer:
                                    checkbox = target.get_by_role("checkbox", name=selected_answer, exact=True)
                                    checkbox.scroll_into_view_if_needed(timeout=5000)
                                    for attempt in range(3):
                                        if checkbox.get_attribute("aria-checked") == "true":
                                            break
                                        checkbox.click(timeout=8000)
                                        for _ in range(10):
                                            if checkbox.get_attribute("aria-checked") == "true":
                                                break
                                            page.wait_for_timeout(100)
                                        if checkbox.get_attribute("aria-checked") != "true" and attempt < 2:
                                            add_job_log(job_id, f'Retrying checkbox “{selected_answer}” for “{question["title"]}” (submission {response_index}).')
                                    if checkbox.get_attribute("aria-checked") != "true":
                                        raise RuntimeError(f'Google Forms did not select checkbox “{selected_answer}”.')
                                    add_job_log(job_id, f"Checked “{selected_answer}” for “{question['title']}” (submission {response_index}).")
                                if not answer:
                                    add_job_log(job_id, f"Left optional checkbox question “{question['title']}” unanswered (submission {response_index}).")
                            elif question.get("type") == "Dropdown":
                                dropdown = target.get_by_role("listbox")
                                card.scroll_into_view_if_needed(timeout=5000)
                                dropdown.click()
                                options = page.get_by_role("option", name=answer, exact=True)
                                option = None
                                for _ in range(20):
                                    for option_index in range(options.count()):
                                        candidate = options.nth(option_index)
                                        if candidate.is_visible():
                                            option = candidate
                                            break
                                    if option is not None:
                                        break
                                    page.wait_for_timeout(100)
                                if option is None:
                                    raise RuntimeError(f'Google Forms did not show dropdown option “{answer}”.')
                                option.click(timeout=8000)
                                add_job_log(job_id, f"Selected “{answer}” for “{question['title']}” (submission {response_index}).")
                            else:
                                radio = target.get_by_role("radio", name=answer, exact=True)
                                if radio.count() == 0 and question.get("groupTitle"):
                                    radio = target.get_by_role("radio", name=re.compile(re.escape(answer), re.I))
                                if radio.count() == 0 and question.get("groupTitle"):
                                    radio = card.get_by_role("radio", name=re.compile(re.escape(answer), re.I))
                                if question.get("groupTitle"):
                                    target.scroll_into_view_if_needed(timeout=5000)
                                    visible_radios = [radio.nth(i) for i in range(radio.count()) if radio.nth(i).is_visible()]
                                    if not visible_radios:
                                        raise RuntimeError(f'Google Forms did not show the grid choice “{answer}” for “{question["title"]}”.')
                                    visible_radios[-1].click(timeout=8000)
                                else:
                                    radio.last.click()
                                add_job_log(job_id, f"Selected “{answer}” for “{question['title']}” (submission {response_index}).")

                        submit_button = page.get_by_role("button", name=re.compile(r"^submit$", re.I))
                        for _ in range(10):
                            if submit_button.count():
                                break
                            next_button = page.get_by_role("button", name=re.compile(r"^next$", re.I))
                            if not next_button.count():
                                break
                            next_button.click()
                            try:
                                submit_button.first.wait_for(state="visible", timeout=6000)
                            except PlaywrightTimeoutError:
                                page.wait_for_timeout(400)
                        if submit_button.count() == 0:
                            raise RuntimeError("Could not find the Google Form Submit button.")
                        update_job(job_id, current=f"Clicking Submit for response {response_index} of {len(responses)}")
                        submit_clicked = True
                        submit_button.click()
                        add_job_log(job_id, f"Clicked the Google Forms Submit button for submission {response_index}.")
                        page.get_by_text(re.compile(r"response has been recorded|your response was recorded", re.I)).wait_for(state="visible", timeout=20000)
                        job = JOBS.get(job_id, {})
                        completed = job.get("completed", 0) + 1
                        update_job(job_id, completed=completed, processed=response_index, current=f"Submission {response_index} of {len(responses)} recorded")
                        add_job_log(job_id, f"Google Forms confirmed submission {response_index} of {len(responses)}.", "success")
                    except Exception as exc:
                        job = JOBS.get(job_id, {})
                        skipped = job.get("skipped", 0) + 1
                        update_job(job_id, skipped=skipped, processed=response_index, current=f"Skipped submission {response_index} of {len(responses)}")
                        detail = str(exc) or "Could not complete this response."
                        if submit_clicked:
                            detail += " Submit was clicked, so check the form to confirm whether Google recorded it."
                        add_job_log(job_id, f"Skipped submission {response_index}: {detail}", "error")
                        add_job_log(job_id, f"Continuing with submission {response_index + 1} of {len(responses)}." if response_index < len(responses) else "No more responses remain.")
                    if response_index < len(responses):
                        time.sleep(0.8)
            finally:
                browser.close()
        job = JOBS.get(job_id, {})
        skipped = job.get("skipped", 0)
        completed = job.get("completed", 0)
        update_job(job_id, status="complete", current="All responses processed")
        add_job_log(job_id, f"Finished processing all responses: {completed} confirmed, {skipped} skipped.", "success" if not skipped else "error")
    except Exception as exc:
        message = str(exc) or "The browser automation stopped unexpectedly."
        completed = JOBS.get(job_id, {}).get("completed", 0)
        add_job_log(job_id, message, "error")
        add_job_log(job_id, f"Automation could not continue after {completed} confirmed submissions. Check the form before retrying to avoid duplicates.", "error")
        update_job(job_id, status="error", error=message, current="Automation stopped")


try:
    if JOBS_FILE.exists():
        for saved_job in json.loads(JOBS_FILE.read_text(encoding="utf-8")):
            if isinstance(saved_job, dict) and saved_job.get("jobId"):
                if saved_job.get("status") in ("queued", "running"):
                    saved_job["status"] = "error"
                    saved_job["error"] = "The local app restarted while this run was in progress. Check the Google Form before retrying."
                    saved_job["current"] = "Interrupted by app restart"
                JOBS[saved_job["jobId"]] = saved_job
except (OSError, json.JSONDecodeError):
    JOBS = {}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _read_runs(self):
        try:
            return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/runs":
            return self._json(200, self._read_runs())
        if self.path == "/api/jobs":
            with JOBS_LOCK:
                return self._json(200, list(reversed(list(JOBS.values()))))
        if self.path.startswith("/api/jobs/"):
            job_id = urlparse(self.path).path.rsplit("/", 1)[-1]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job:
                    return self._json(200, job)
            return self._json(404, {"error": "Submission log not found"})
        if self.path == "/api/google/status":
            configured = bool(os.environ.get("GOOGLE_CLIENT_ID", "").strip() and os.environ.get("GOOGLE_CLIENT_SECRET", "").strip())
            return self._json(200, {"connected": bool(load_token()), "configured": configured})
        if self.path in ("/api/auth/google", "/auth/google"):
            global OAUTH_STATE
            try:
                config = oauth_config()
                OAUTH_STATE = secrets.token_urlsafe(32)
                auth_uri = config.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth")
                params = {"client_id": config["client_id"], "redirect_uri": config["redirect_uri"],
                          "response_type": "code", "scope": SCOPE, "access_type": "offline",
                          "include_granted_scopes": "true", "prompt": "consent", "state": OAUTH_STATE}
                self.send_response(302)
                self.send_header("Location", auth_uri + "?" + urlencode(params))
                self.end_headers()
            except (RuntimeError, json.JSONDecodeError) as exc:
                self.send_error(500, str(exc))
            return
        if urlparse(self.path).path == "/api/auth/callback/google":
            query = parse_qs(urlparse(self.path).query)
            if query.get("state", [None])[0] != OAUTH_STATE or not OAUTH_STATE:
                self.send_error(400, "OAuth state did not match. Please try connecting again.")
                return
            OAUTH_STATE = None
            if query.get("error"):
                self.send_response(302); self.send_header("Location", "/?google=error"); self.end_headers(); return
            try:
                config = oauth_config()
                result = post_google_form(config.get("token_uri", "https://oauth2.googleapis.com/token"), {
                    "code": query.get("code", [""])[0], "client_id": config["client_id"],
                    "client_secret": config["client_secret"], "redirect_uri": config["redirect_uri"],
                    "grant_type": "authorization_code"})
                result["expires_at"] = time.time() + int(result.get("expires_in", 3600))
                old = load_token() or {}
                if not result.get("refresh_token") and old.get("refresh_token"):
                    result["refresh_token"] = old["refresh_token"]
                save_token(result)
                self.send_response(302); self.send_header("Location", "/?google=connected"); self.end_headers()
            except (HTTPError, URLError, RuntimeError, json.JSONDecodeError, KeyError):
                self.send_error(502, "Google sign-in could not be completed. Check .env and the authorized redirect URI.")
            return
        if self.path.startswith("/api/"):
            return self._json(404, {"error": "Unknown API route"})
        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/forms/submit":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 5_000_000:
                    return self._json(413, {"error": "Invalid request size"})
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                responder_uri = body.get("responderUri", "")
                parsed = urlparse(responder_uri)
                target_form_id = resolve_form_id(responder_uri)
                source_form_id = resolve_form_id(body.get("url", ""))
                responses = body.get("responses", [])
                questions = body.get("questions", [])
                if target_form_id != LIVE_TEST_FORM_RESPONDER_ID or source_form_id != LIVE_TEST_FORM_SOURCE_ID:
                    return self._json(403, {"error": "Live submission is enabled only for the configured FormLab test form. Other Google Forms remain preview-only."})
                if not body.get("confirmedTestForm"):
                    return self._json(400, {"error": "Confirm that this is a test form you control before submitting."})
                if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or "/forms/" not in parsed.path:
                    return self._json(400, {"error": "The responder page must be a Google Forms page."})
                if not isinstance(responses, list) or not 1 <= len(responses) <= 50:
                    return self._json(400, {"error": "Choose between 1 and 50 test submissions per run."})
                if not isinstance(questions, list) or not questions or any(len(row.get("answers", [])) != len(questions) for row in responses):
                    return self._json(400, {"error": "The preview responses do not match the loaded questions."})
                job_id = str(uuid.uuid4())
                job = {"jobId": job_id, "status": "queued", "title": body.get("title", "Google Form"),
                       "total": len(responses), "completed": 0, "current": "Queued", "error": "", "logs": []}
                with JOBS_LOCK:
                    JOBS[job_id] = job
                add_job_log(job_id, "Submission run queued. The app will open the form, select answers, and click Submit for each response.")
                worker = threading.Thread(target=submit_responses, args=(job_id, responder_uri, job["title"], questions, responses), daemon=True)
                worker.start()
                return self._json(202, {"jobId": job_id, "status": "queued"})
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                return self._json(400, {"error": "Invalid submission request."})
        if self.path == "/api/forms/load":
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 20_000:
                    return self._json(413, {"error": "Invalid request size"})
                body = json.loads(self.rfile.read(size).decode("utf-8"))
                result = read_form(body.get("url", ""))
                return self._json(200, result)
            except PermissionError as exc:
                code = 401 if "Connect your Google account" in str(exc) or "sign-in expired" in str(exc) else 403
                return self._json(code, {"error": str(exc)})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
                return self._json(400, {"error": "Request must include a Google Form URL."})
            except (HTTPError, URLError, RuntimeError) as exc:
                return self._json(502, {"error": str(exc) or "Could not reach Google Forms."})
        if self.path != "/api/runs":
            return self._json(404, {"error": "Unknown API route"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 5_000_000:
                return self._json(413, {"error": "Invalid request size"})
            run = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(run, dict) or not isinstance(run.get("responses"), list):
                return self._json(400, {"error": "A test run with responses is required"})
            runs = self._read_runs()
            runs.insert(0, run)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            RUNS_FILE.write_text(json.dumps(runs[:50], ensure_ascii=False, indent=2), encoding="utf-8")
            return self._json(201, run)
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"error": "Request body must be valid JSON"})

    def do_DELETE(self):
        if self.path != "/api/runs":
            return self._json(404, {"error": "Unknown API route"})
        RUNS_FILE.unlink(missing_ok=True)
        with JOBS_LOCK:
            active_jobs = {
                job_id: job for job_id, job in JOBS.items()
                if job.get("status") in ("queued", "running")
            }
            JOBS.clear()
            JOBS.update(active_jobs)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            JOBS_FILE.write_text(
                json.dumps(list(JOBS.values())[:30], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return self._json(200, {"ok": True})


if __name__ == "__main__":
    address = ("127.0.0.1", 8000)
    print(f"FormLab sandbox is running at http://{address[0]}:{address[1]}")
    ThreadingHTTPServer(address, Handler).serve_forever()
