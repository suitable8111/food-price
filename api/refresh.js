// 사이트의 "최신화" 버튼이 호출하는 Vercel 서버 함수.
// POST: GitHub Actions 의 refresh.yml 을 실행 (쿨다운 적용)
// GET : 가장 최근 실행 상태를 돌려준다
//
// Vercel 환경변수
//   GITHUB_TOKEN  필수. food-price 레포의 Actions: Read and write 권한이 있는 fine-grained 토큰
//   GITHUB_REPO   선택. 기본값 suitable8111/food-price
const REPO = process.env.GITHUB_REPO || "suitable8111/food-price";
const WORKFLOW = "refresh.yml";
const COOLDOWN_MS = 10 * 60 * 1000;

async function github(path, init = {}) {
  const res = await fetch(`https://api.github.com/repos/${REPO}${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "food-price-refresh",
      ...init.headers,
    },
  });
  if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}

async function latestRun() {
  const data = await github(`/actions/workflows/${WORKFLOW}/runs?per_page=1`);
  const run = data.workflow_runs[0];
  if (!run) return null;
  return {
    id: run.id,
    status: run.status,            // queued | in_progress | completed
    conclusion: run.conclusion,    // success | failure | null
    created_at: run.created_at,
    updated_at: run.updated_at,
  };
}

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (!process.env.GITHUB_TOKEN) {
    return res.status(503).json({ error: "not_configured", message: "서버에 GITHUB_TOKEN 이 설정되지 않았습니다." });
  }
  try {
    const run = await latestRun();
    if (req.method === "GET") return res.status(200).json({ run });
    if (req.method !== "POST") return res.status(405).json({ error: "method_not_allowed" });

    if (run && run.status !== "completed") {
      return res.status(202).json({ started: false, reason: "running", run });
    }
    const since = run ? Date.now() - Date.parse(run.created_at) : Infinity;
    if (since < COOLDOWN_MS) {
      return res.status(429).json({
        started: false, reason: "cooldown", run,
        retry_after: Math.ceil((COOLDOWN_MS - since) / 1000),
      });
    }
    const requestedAt = new Date().toISOString();
    await github(`/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main" }),
    });
    return res.status(202).json({ started: true, requested_at: requestedAt });
  } catch (err) {
    console.error(err);
    return res.status(502).json({ error: "github_error", message: "GitHub 요청에 실패했습니다." });
  }
};
