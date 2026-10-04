# facts.json schema

`facts.json` holds every answer the harness may put on a form. It is
gitignored; start from `facts.example.json` and fill in your details.
The harness never invents facts: any form field with no matching key here
is skipped.

## Identity

| Key | Example | Used for |
|---|---|---|
| `first_name`, `last_name`, `full_name` | `"Alex"`, `"Carter"`, `"Alex Carter"` | Name fields |
| `email` | `"alex.carter@example.com"` | Email field |
| `phone` | `"5551234567"` | Phone (digits; formatted per ATS) |
| `city`, `state`, `country`, `location` | `"Austin"`, `"Texas"`, `"United States"`, `"Austin, Texas, United States"` | Location fields |
| `linkedin`, `github`, `website` | URLs | Profile links |
| `resume_path` | `"/home/user/resume.pdf"` | Resume upload |

## Work history

| Key | Example |
|---|---|
| `current_title`, `current_employer`, `current_employer_dates` | `"Senior Software Engineer"`, `"Acme Corp"`, `"Jan 2020 to present"` |
| `previous_title`, `previous_employer`, `previous_employer_dates` | `"Software Engineer"`, `"Startup Inc"`, `"Jun 2017 to Dec 2019"` |
| `years_experience` | `"8+"` |
| `previously_employed_here` | `"No"` |
| `referral` | `""` (name, if you have one) |
| `job_source` | `"LinkedIn"` |

## Education

`education` is a list of objects with `degree`, `school`, `end_year`:

```json
"education": [
  {"degree": "BS Computer Science", "school": "State University", "end_year": "2019"}
]
```

## Work authorization

| Key | Example | Notes |
|---|---|---|
| `work_authorization` | `"US citizen"` | Free-text; also drives sponsorship answers |

Answer truthfully. The harness maps sponsorship questions from this field
and never claims an authorization you do not have.

## Screening posture

| Key | Example | Notes |
|---|---|---|
| `willing_relocate`, `willing_in_office`, `willing_hybrid`, `willing_travel`, `willing_start_date_flexible` | `"Yes"` | Default yes; set `"No"` where you mean it |
| `ai_agent_disclosure` | `"No"` | Your call |
| `salary_expectation` | `"$150K-$180K base"` | Free text |
| `salary_midpoint` | `165000` | Numeric, for range questions |
| `veteran` | `"not a veteran"` | Verbatim answer text |
| `disability` | `"no disability"` | Verbatim answer text |
| `ethnicity` | `"not Hispanic or Latino"` | Verbatim answer text |
| `pronouns` | `"I prefer not to say"` | |

## Rules

- Every value must be true. The harness copies these verbatim; it does
  not interpret, inflate, or soften them.
- Leave a key empty (`""`) if you do not want it used. Empty values are
  never submitted.
- `resume_path` must point to a PDF the browser host can read. In batch
  mode, `--resume-map` overrides it per posting.
