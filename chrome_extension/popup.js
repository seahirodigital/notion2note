const DEFAULTS = {
  owner: "seahirodigital",
  repo: "notion2note",
  workflow: "notion-note-post.yml",
  ref: "main",
  token: "",
};

const fields = {
  owner: document.getElementById("owner"),
  repo: document.getElementById("repo"),
  workflow: document.getElementById("workflow"),
  ref: document.getElementById("ref"),
  token: document.getElementById("token"),
  pageUrl: document.getElementById("pageUrl"),
  publish: document.getElementById("publish"),
  dryRunPublish: document.getElementById("dryRunPublish"),
  noTopImage: document.getElementById("noTopImage"),
  dispatch: document.getElementById("dispatch"),
  status: document.getElementById("status"),
};

function setStatus(message) {
  fields.status.textContent = message;
}

async function loadCurrentTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.url || "";
}

async function loadOptions() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  for (const key of ["owner", "repo", "workflow", "ref", "token"]) {
    fields[key].value = stored[key] || DEFAULTS[key];
  }
  fields.pageUrl.value = await loadCurrentTabUrl();
}

async function saveOptions() {
  const payload = {};
  for (const key of ["owner", "repo", "workflow", "ref", "token"]) {
    payload[key] = fields[key].value.trim();
  }
  await chrome.storage.local.set(payload);
}

function requiredValue(key, label) {
  const value = fields[key].value.trim();
  if (!value) {
    throw new Error(`${label}を入力してください。`);
  }
  return value;
}

async function dispatchWorkflow() {
  const owner = requiredValue("owner", "GitHub Owner");
  const repo = requiredValue("repo", "Repository");
  const workflow = requiredValue("workflow", "Workflow");
  const ref = requiredValue("ref", "Branch");
  const token = requiredValue("token", "GitHub Token");
  const pageUrl = requiredValue("pageUrl", "Notion URL");

  await saveOptions();
  const response = await fetch(
    `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref,
        inputs: {
          notion_page_url: pageUrl,
          publish: String(fields.publish.checked),
          dry_run_publish: String(fields.dryRunPublish.checked),
          no_top_image: String(fields.noTopImage.checked),
        },
      }),
    }
  );

  if (response.status !== 204) {
    const text = await response.text();
    throw new Error(`GitHub API ${response.status}: ${text.slice(0, 300)}`);
  }
}

fields.dispatch.addEventListener("click", async () => {
  fields.dispatch.disabled = true;
  setStatus("起動しています...");
  try {
    await dispatchWorkflow();
    setStatus("GitHub Actionsを起動しました。");
  } catch (error) {
    setStatus(error?.message || String(error));
  } finally {
    fields.dispatch.disabled = false;
  }
});

loadOptions().catch((error) => setStatus(error?.message || String(error)));
