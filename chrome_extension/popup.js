const DEFAULTS = {
  owner: "seahirodigital",
  repo: "notion2note",
  workflow: "notion-note-post.yml",
  ref: "main",
  token: "",
  pageUrl: "",
  noteTitle: "",
};

const TEXT_KEYS = ["owner", "repo", "workflow", "ref", "token", "pageUrl", "noteTitle"];
const ONEDRIVE_REPO_DIR = "C:\\Users\\mahha\\OneDrive\\開発\\notion2note";
const QUICK_LINK_PATHS = {
  affiliate: `${ONEDRIVE_REPO_DIR}\\affiliate_links.txt`,
  tag: `${ONEDRIVE_REPO_DIR}\\tag.md`,
};

const fields = {
  owner: document.getElementById("owner"),
  repo: document.getElementById("repo"),
  workflow: document.getElementById("workflow"),
  ref: document.getElementById("ref"),
  token: document.getElementById("token"),
  pageUrl: document.getElementById("pageUrl"),
  noteTitle: document.getElementById("noteTitle"),
  draftDispatch: document.getElementById("draftDispatch"),
  publishDispatch: document.getElementById("publishDispatch"),
  loadUrl: document.getElementById("loadUrl"),
  status: document.getElementById("status"),
  affiliateLink: document.getElementById("affiliateLink"),
  tagLink: document.getElementById("tagLink"),
};

let saveTimer = 0;

function setStatus(message) {
  fields.status.textContent = message;
}

function storagePayload() {
  const payload = {};
  for (const key of TEXT_KEYS) {
    payload[key] = fields[key].value.trim();
  }
  return payload;
}

async function saveOptions() {
  await chrome.storage.local.set(storagePayload());
}

function saveOptionsSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveOptions().catch((error) => setStatus(error?.message || String(error)));
  }, 180);
}

async function loadCurrentTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.url || "";
}

function isNotionUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.endsWith("notion.so") || parsed.hostname.endsWith("notion.site");
  } catch {
    return false;
  }
}

function localFileHref(path) {
  const normalized = String(path || "").replace(/\\/g, "/");
  const parts = normalized.split("/");
  const encodedPath = parts
    .map((part, index) => (index === 0 ? part : encodeURIComponent(part)))
    .join("/");
  return `file:///${encodedPath}`;
}

function updateQuickLinks() {
  fields.affiliateLink.href = localFileHref(QUICK_LINK_PATHS.affiliate);
  fields.affiliateLink.title = `アフィリエイトリンクファイルを開く: ${QUICK_LINK_PATHS.affiliate}`;
  fields.tagLink.href = localFileHref(QUICK_LINK_PATHS.tag);
  fields.tagLink.title = `タグファイルを開く: ${QUICK_LINK_PATHS.tag}`;
}

async function refreshCurrentTabUrl(force = false) {
  const currentUrl = await loadCurrentTabUrl();
  if (!currentUrl) {
    return "";
  }
  if (force || isNotionUrl(currentUrl)) {
    fields.pageUrl.value = currentUrl;
    await saveOptions();
  }
  return currentUrl;
}

async function loadOptions() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  for (const key of TEXT_KEYS) {
    fields[key].value = stored[key] ?? DEFAULTS[key];
  }
  updateQuickLinks();
  await refreshCurrentTabUrl(false);
  updateQuickLinks();
}

function requiredValue(key, label) {
  const value = fields[key].value.trim();
  if (!value) {
    throw new Error(`${label}を入力してください。`);
  }
  return value;
}

function setPostingButtonsDisabled(disabled) {
  fields.draftDispatch.disabled = disabled;
  fields.publishDispatch.disabled = disabled;
}

async function dispatchWorkflow({ publish }) {
  const owner = requiredValue("owner", "GitHub Owner");
  const repo = requiredValue("repo", "Repository");
  const workflow = requiredValue("workflow", "Workflow");
  const ref = requiredValue("ref", "Branch");
  const token = requiredValue("token", "GitHub Token");
  const pageUrl = requiredValue("pageUrl", "Notion URL");
  const noteTitle = fields.noteTitle.value.trim();

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
          note_title: noteTitle,
          publish: String(Boolean(publish)),
          dry_run_publish: "false",
          no_top_image: "false",
        },
      }),
    }
  );

  if (response.status !== 204) {
    const text = await response.text();
    throw new Error(`GitHub API ${response.status}: ${text.slice(0, 300)}`);
  }
}

for (const key of TEXT_KEYS) {
  fields[key].addEventListener("input", () => {
    if (key === "owner" || key === "repo" || key === "ref") {
      updateQuickLinks();
    }
    saveOptionsSoon();
  });
}

fields.loadUrl.addEventListener("click", async () => {
  fields.loadUrl.disabled = true;
  try {
    const currentUrl = await refreshCurrentTabUrl(true);
    setStatus(currentUrl ? "現在のタブURLを取得しました。" : "現在のタブURLを取得できませんでした。");
  } catch (error) {
    setStatus(error?.message || String(error));
  } finally {
    fields.loadUrl.disabled = false;
  }
});

fields.draftDispatch.addEventListener("click", async () => {
  setPostingButtonsDisabled(true);
  setStatus("下書き投稿を開始しています...");
  try {
    await dispatchWorkflow({ publish: false });
    setStatus("下書き投稿を開始しました。");
  } catch (error) {
    setStatus(error?.message || String(error));
  } finally {
    setPostingButtonsDisabled(false);
  }
});

fields.publishDispatch.addEventListener("click", async () => {
  setPostingButtonsDisabled(true);
  setStatus("本番投稿を開始しています...");
  try {
    await dispatchWorkflow({ publish: true });
    setStatus("本番投稿を開始しました。完了後にDiscordへ通知します。");
  } catch (error) {
    setStatus(error?.message || String(error));
  } finally {
    setPostingButtonsDisabled(false);
  }
});

loadOptions().catch((error) => setStatus(error?.message || String(error)));
