# FormLab

FormLab builds sample response sets from Google Forms and can submit them through the form's responder page. A live run creates **real response entries** in that Google Form and any linked response sheet. Use a separate test copy that you control.

## Start the app

Double-click `run.bat`, then open <http://127.0.0.1:8000>. The first launch creates a local Python environment and installs Playwright and its Chromium browser; it can take a few minutes. Later launches reuse that setup.

The sample form is local-only. For a Google Form, connect Google, paste its edit or responder link, load the form, set each distribution to 100%, choose a submission count (1–50), and review the generated answers. Confirm that you control the selected form before starting: submissions are real entries in that form, not local previews.

## Google setup

OAuth is used to read the form's question structure. The browser runner opens the responder URL and clicks the visible options and Submit button.

1. In [Google Cloud Console](https://console.cloud.google.com/), enable **Google Forms API** and configure the OAuth consent screen. Add the account you will use as a test user if the consent screen is in testing mode.
2. Create a **Web application** OAuth client and add this exact authorized redirect URI: `http://127.0.0.1:8000/api/auth/callback/google`.
3. Put the client ID and secret in `.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The other values and redirect URI are already present in `.env`.
4. Restart `run.bat`, click **Connect Google**, authorize the read-only `forms.body.readonly` permission, and load a form you can view. The runner obtains its responder URL from the Forms API, so edit links such as `https://docs.google.com/forms/d/<form-id>/edit` work as input.

Google's official Forms API has no response-create method; FormLab's test runner uses browser automation to submit through the responder page. [Forms API response methods](https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses) lists `get` and `list`.

## Supported questions and logs

The runner supports multiple-choice, dropdown, checkbox, scale, rating, and choice-grid questions. Required free-text, date, time, file-upload, or other unsupported questions block import. If Google Forms requires sign-in or displays a verification challenge, the runner stops and records the issue; it does not bypass the challenge. A failure can happen after some submissions have completed, so check the form's response summary before retrying.

Submission progress and per-question actions are shown in the app while it runs. Logs are saved to `data/submission-jobs.json`; local sample previews are saved in `data/test-runs.json`. 