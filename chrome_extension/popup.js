const DEFAULTS = {
  owner: "seahirodigital",
  repo: "notion2note",
  workflow: "notion-note-post.yml",
  ref: "main",
  token: "",
  pageUrl: "",
  publish: false,
  dryRunPublish: false,
  noTopImage: false,
};

const TEXT_KEYS = ["owner", "repo", "workflow", "ref", "token", "pageUrl"];
const CHECK_KEYS = ["publish", "dryRunPublish", "noTopImage"];

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
  for (const key of CHECK_KEYS) {
    payload[key] = fields[key].checked;
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

function updateQuickLinks() {
  const owner = fields.owner.value.trim() || DEFAULTS.owner;
  const repo = fields.repo.value.trim() || DEFAULTS.repo;
  const ref = fields.ref.value.trim() || DEFAULTS.ref;
  const refPath = encodeURIComponent(ref).replace(/%2F/g, "/");
  const baseUrl = `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/blob/${refPath}`;
  fields.affiliateLink.href = `${baseUrl}/affiliate_links.txt`;
  fields.affiliateLink.title = `アフィリエイトリンクファイルを開く: ${fields.affiliateLink.href}`;
  fields.tagLink.href = `${baseUrl}/tag.md`;
  fields.tagLink.title = `タグファイルを開く: ${fields.tagLink.href}`;
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
  for (const key of CHECK_KEYS) {
    fields[key].checked = Boolean(stored[key]);
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

for (const key of TEXT_KEYS) {
  fields[key].addEventListener("input", () => {
    if (key === "owner" || key === "repo" || key === "ref") {
      updateQuickLinks();
    }
    saveOptionsSoon();
  });
}

for (const key of CHECK_KEYS) {
  fields[key].addEventListener("change", () => {
    saveOptions().catch((error) => setStatus(error?.message || String(error)));
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

fields.dispatch.addEventListener("click", async () => {
  fields.dispatch.disabled = true;
  setStatus("Actionsを起動しています...");
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
