from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


GRAPHQL_URL = "https://api.github.com/graphql"
OUTPUT_PATH = Path("assets/github-activity.svg")
USERNAME = os.getenv("GITHUB_USERNAME", "Ariasu123")
TOKEN = os.getenv("GITHUB_TOKEN")

BACKGROUND = "#0d1117"
SURFACE = "#161b22"
BLUE = "#58a6ff"
WHITE = "#e6edf3"
MUTED = "#8b949e"
LINE = "#30363d"


PROFILE_QUERY = """
query Profile($login: String!) {
  user(login: $login) {
    pullRequests(first: 1) {
      totalCount
    }
    issues(first: 1) {
      totalCount
    }
    contributionsCollection {
      contributionYears
    }
    repositories(
      first: 100
      ownerAffiliations: OWNER
      privacy: PUBLIC
      isFork: false
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes {
        stargazerCount
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
  }
}
"""


CONTRIBUTIONS_QUERY = """
query Contributions($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def graphql_request(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is missing")

    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "ariasu-profile-statistics",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Could not contact GitHub API: {error}") from error

    if result.get("errors"):
        raise RuntimeError(f"GraphQL errors: {result['errors']}")

    return result["data"]


def contribution_period(year: int, now: datetime) -> tuple[str, str]:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    if year == now.year:
        end = now
    else:
        end = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    return start.isoformat(), end.isoformat()


def calculate_streaks(days: list[dict[str, Any]]) -> tuple[int, int]:
    counts = {
        datetime.strptime(item["date"], "%Y-%m-%d").date(): int(
            item["contributionCount"]
        )
        for item in days
    }
    if not counts:
        return 0, 0

    longest = 0
    running = 0
    previous: date | None = None

    for current in sorted(counts):
        if counts[current] > 0:
            if previous is not None and current == previous + timedelta(days=1):
                running += 1
            else:
                running = 1
            longest = max(longest, running)
            previous = current
        else:
            running = 0
            previous = None

    cursor = datetime.now(timezone.utc).date()
    if counts.get(cursor, 0) == 0:
        cursor -= timedelta(days=1)

    current_streak = 0
    while counts.get(cursor, 0) > 0:
        current_streak += 1
        cursor -= timedelta(days=1)

    return current_streak, longest


def collect_languages(repositories: list[dict[str, Any]]) -> list[tuple[str, float, str]]:
    totals: Counter[str] = Counter()
    colors: dict[str, str] = {}

    for repository in repositories:
        for edge in repository["languages"]["edges"]:
            name = edge["node"]["name"]
            totals[name] += int(edge["size"])
            colors[name] = edge["node"].get("color") or BLUE

    total_size = sum(totals.values())
    if total_size == 0:
        return []

    ranked = totals.most_common()
    if len(ranked) > 6:
        visible = ranked[:5]
        visible.append(("Other", sum(size for _, size in ranked[5:])))
        colors["Other"] = MUTED
    else:
        visible = ranked

    return [
        (name, size / total_size * 100, colors[name])
        for name, size in visible
    ]


def fetch_statistics() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    profile = graphql_request(PROFILE_QUERY, {"login": USERNAME})["user"]
    if profile is None:
        raise RuntimeError(f"GitHub user '{USERNAME}' was not found")

    repositories = profile["repositories"]["nodes"]
    years = sorted(set(profile["contributionsCollection"]["contributionYears"]))
    if now.year not in years:
        years.append(now.year)

    total_commits = 0
    total_contributions = 0
    contributions_this_year = 0
    all_days: list[dict[str, Any]] = []

    for year in years:
        start, end = contribution_period(year, now)
        collection = graphql_request(
            CONTRIBUTIONS_QUERY,
            {"login": USERNAME, "from": start, "to": end},
        )["user"]["contributionsCollection"]
        calendar = collection["contributionCalendar"]
        total_commits += int(collection["totalCommitContributions"])
        total_contributions += int(calendar["totalContributions"])
        if year == now.year:
            contributions_this_year = int(calendar["totalContributions"])
        for week in calendar["weeks"]:
            all_days.extend(week["contributionDays"])

    current_streak, longest_streak = calculate_streaks(all_days)

    return {
        "stars": sum(int(repo["stargazerCount"]) for repo in repositories),
        "commits": total_commits,
        "pull_requests": int(profile["pullRequests"]["totalCount"]),
        "issues": int(profile["issues"]["totalCount"]),
        "contributions_this_year": contributions_this_year,
        "total_contributions": total_contributions,
        "current_streak": current_streak,
        "longest_streak": longest_streak,
        "languages": collect_languages(repositories),
    }


def statistic_row(label: str, value: int, y: int) -> str:
    return f"""
    <circle cx="40" cy="{y - 5}" r="4" fill="{BLUE}"/>
    <text x="55" y="{y}" class="stat-label">{escape(label)}</text>
    <text x="455" y="{y}" text-anchor="end" class="stat-value">{value:,}</text>
    """


def language_graph(
    languages: list[tuple[str, float, str]],
) -> tuple[str, str]:
    if not languages:
        languages = [("No public data", 100.0, MUTED)]

    bar_x = 535
    bar_y = 94
    bar_width = 405
    cursor = float(bar_x)
    segments: list[str] = []
    legend: list[str] = []

    for index, (name, percentage, color) in enumerate(languages):
        width = (
            bar_x + bar_width - cursor
            if index == len(languages) - 1
            else bar_width * percentage / 100
        )
        segments.append(
            f'<rect x="{cursor:.2f}" y="{bar_y}" width="{max(width, 1.5):.2f}" '
            f'height="8" fill="{escape(color)}"/>'
        )
        cursor += width

        column = index % 2
        row = index // 2
        legend_x = bar_x + column * 205
        legend_y = 132 + row * 30
        legend.append(
            f"""
            <circle cx="{legend_x + 4}" cy="{legend_y - 4}" r="4" fill="{escape(color)}"/>
            <text x="{legend_x + 16}" y="{legend_y}" class="legend-label">{escape(name)}</text>
            <text x="{legend_x + 185}" y="{legend_y}" text-anchor="end" class="legend-value">{percentage:.1f}%</text>
            """
        )

    return "\n".join(segments), "\n".join(legend)


def build_svg(statistics: dict[str, Any]) -> str:
    language_segments, language_legend = language_graph(statistics["languages"])
    rows = "\n".join(
        [
            statistic_row("Total stars earned", statistics["stars"], 90),
            statistic_row("Total commits", statistics["commits"], 120),
            statistic_row("Total pull requests", statistics["pull_requests"], 150),
            statistic_row("Total issues", statistics["issues"], 180),
            statistic_row(
                "Contributions this year",
                statistics["contributions_this_year"],
                210,
            ),
        ]
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="340" viewBox="0 0 1000 340" role="img" aria-labelledby="title description">
  <title id="title">{escape(USERNAME)} development metrics</title>
  <desc id="description">Automatically updated public GitHub statistics, language usage, and contribution streaks.</desc>
  <defs>
    <clipPath id="language-bar"><rect x="535" y="94" width="405" height="8" rx="4"/></clipPath>
  </defs>
  <style>
    .section-title {{ font: 500 18px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; fill: {BLUE}; }}
    .stat-label, .stat-value {{ font: 600 14px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; fill: {WHITE}; }}
    .legend-label {{ font: 400 12px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; fill: {WHITE}; }}
    .legend-value {{ font: 700 12px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; fill: {WHITE}; }}
    .metric-number {{ font: 700 25px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; fill: {BLUE}; }}
    .metric-label {{ font: 600 13px -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; fill: {WHITE}; }}
  </style>
  <rect width="1000" height="340" rx="16" fill="{BACKGROUND}"/>
  <rect x="18" y="18" width="964" height="304" rx="12" fill="none" stroke="{LINE}"/>
  <text x="40" y="55" class="section-title">GitHub Stats</text>
  <text x="535" y="55" class="section-title">Most Used Languages</text>
  <line x1="500" y1="38" x2="500" y2="222" stroke="{LINE}"/>
  {rows}
  <rect x="535" y="94" width="405" height="8" rx="4" fill="{SURFACE}"/>
  <g clip-path="url(#language-bar)">{language_segments}</g>
  {language_legend}
  <line x1="333" y1="238" x2="333" y2="305" stroke="{LINE}"/>
  <line x1="667" y1="238" x2="667" y2="305" stroke="{LINE}"/>
  <text x="167" y="273" text-anchor="middle" class="metric-number">{statistics['total_contributions']:,}</text>
  <text x="167" y="299" text-anchor="middle" class="metric-label">Total contributions</text>
  <text x="500" y="273" text-anchor="middle" class="metric-number">{statistics['current_streak']}</text>
  <text x="500" y="299" text-anchor="middle" class="metric-label">Current streak</text>
  <text x="834" y="273" text-anchor="middle" class="metric-number">{statistics['longest_streak']}</text>
  <text x="834" y="299" text-anchor="middle" class="metric-label">Longest streak</text>
</svg>
"""


def main() -> int:
    try:
        generated = build_svg(fetch_statistics())
        content = "\n".join(line.rstrip() for line in generated.splitlines()) + "\n"
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if OUTPUT_PATH.exists() and OUTPUT_PATH.read_text(encoding="utf-8") == content:
            print(f"No changes for {OUTPUT_PATH}")
            return 0
        OUTPUT_PATH.write_text(content, encoding="utf-8")
        print(f"Updated {OUTPUT_PATH}")
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
