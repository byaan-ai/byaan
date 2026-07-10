def _suggestion_rows_html(suggestions: list[dict]) -> str:
    if not suggestions:
        return '<p class="message">No pending suggestions right now.</p>'

    items = []
    for s in suggestions:
        title = s.get("title", "Untitled suggestion")
        skill_name = s.get("skill_name") or "New skill"
        items.append(
            f'<li class="suggestion-item"><strong>{title}</strong>'
            f'<span class="suggestion-skill"> — {skill_name}</span></li>'
        )
    return f'<ul class="suggestion-list">{"".join(items)}</ul>'


def get_skill_digest_html(
    tenant_name: str,
    stats: dict,
    suggestions: list[dict],
    frontend_url: str,
) -> str:
    review_link = f"{frontend_url}/skill-review"
    evaluated = stats.get("evaluated", 0)
    confirmed = stats.get("confirmed", 0)
    mistake = stats.get("mistake", 0)
    questions = stats.get("questions", 0)

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Skill Learning Digest</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
      background-color: #f5f5f5;
      line-height: 1.6;
    }}
    .email-wrapper {{ background-color: #f5f5f5; min-height: 100vh; padding-top: 40px; }}
    .header {{
      background-color: #f97316; padding: 30px 40px; text-align: center;
      max-width: 600px; margin: 0 auto; border-radius: 8px 8px 0 0;
    }}
    .logo {{ color: white; font-size: 28px; font-weight: bold; letter-spacing: 1px; }}
    .content-wrapper {{ max-width: 600px; margin: 0 auto; }}
    .content-card {{
      background-color: white; margin: 0 0 40px 0; padding: 40px 35px;
      border-radius: 0 0 8px 8px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
    }}
    .greeting {{ font-size: 24px; font-weight: 400; color: #1f2937; margin-bottom: 20px; text-align: center; }}
    .message {{ color: #4b5563; font-size: 15px; margin-bottom: 12px; line-height: 1.7; }}
    .stats {{ display: table; width: 100%; margin: 24px 0; border-collapse: separate; border-spacing: 8px; }}
    .stat-row {{ display: table-row; }}
    .stat-cell {{
      display: table-cell; width: 25%; text-align: center; padding: 16px 8px;
      background-color: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;
    }}
    .stat-number {{ font-size: 26px; font-weight: 700; color: #1f2937; }}
    .stat-label {{ font-size: 12px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; }}
    .section-title {{ font-size: 16px; font-weight: 600; color: #1f2937; margin: 28px 0 12px 0; }}
    .suggestion-list {{ list-style: none; }}
    .suggestion-item {{
      padding: 12px 14px; margin-bottom: 8px; background-color: #f9fafb;
      border-left: 3px solid #f97316; border-radius: 4px; font-size: 14px; color: #1f2937;
    }}
    .suggestion-skill {{ color: #6b7280; }}
    .button-wrapper {{ text-align: center; margin: 32px 0 12px 0; }}
    .review-button {{
      display: inline-block; background-color: #f97316; color: white !important; text-decoration: none;
      padding: 14px 30px; border-radius: 6px; font-size: 16px; font-weight: 600;
    }}
    .footer {{
      text-align: center; color: #6b7280; font-size: 14px; margin-top: 30px;
      padding-top: 20px; border-top: 1px solid #e5e7eb;
    }}
  </style>
</head>
<body>
  <div class="email-wrapper">
    <div class="header">
      <div class="logo">Byaan</div>
    </div>
    <div class="content-wrapper">
      <div class="content-card">
        <h1 class="greeting">Skill Learning Digest</h1>
        <p class="message">Here is today's summary for <strong>{tenant_name}</strong>.</p>

        <div class="stats">
          <div class="stat-row">
            <div class="stat-cell"><div class="stat-number">{evaluated}</div><div class="stat-label">Evaluated</div></div>
            <div class="stat-cell"><div class="stat-number">{confirmed}</div><div class="stat-label">Confirmed</div></div>
            <div class="stat-cell"><div class="stat-number">{mistake}</div><div class="stat-label">Mistakes</div></div>
            <div class="stat-cell"><div class="stat-number">{questions}</div><div class="stat-label">Questions</div></div>
          </div>
        </div>

        <div class="section-title">Pending suggestions</div>
        {_suggestion_rows_html(suggestions)}

        <div class="button-wrapper">
          <a href="{review_link}" class="review-button">Review Suggestions</a>
        </div>

        <div class="footer">
          <p>You are receiving this because you are an owner or admin of {tenant_name}.</p>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
""".strip()


def get_skill_digest_text(
    tenant_name: str,
    stats: dict,
    suggestions: list[dict],
    frontend_url: str,
) -> str:
    review_link = f"{frontend_url}/skill-review"
    lines = [
        f"Skill Learning Digest for {tenant_name}",
        "",
        f"Evaluated: {stats.get('evaluated', 0)}",
        f"Confirmed: {stats.get('confirmed', 0)}",
        f"Mistakes: {stats.get('mistake', 0)}",
        f"Questions: {stats.get('questions', 0)}",
        "",
        "Pending suggestions:",
    ]
    if suggestions:
        for s in suggestions:
            skill_name = s.get("skill_name") or "New skill"
            lines.append(f"- {s.get('title', 'Untitled suggestion')} — {skill_name}")
    else:
        lines.append("- None")

    lines += ["", f"Review them here: {review_link}"]
    return "\n".join(lines).strip()
