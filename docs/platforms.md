# Job Discovery Platforms

Where to find job postings to feed into ai-job-pilot. Organized by priority.
All of these are sources of posting URLs; ai-job-pilot handles the application itself.

## API-first discovery

These return structured data, no DOM scraping needed.

- **AI Dev Jobs** (https://aidevboard.com) - 9,000+ AI/ML engineering jobs scraped daily from 580 company ATS sources. Free public REST API (`GET /api/v1/jobs`) with tag filters, no auth for reads, OpenAPI spec published. Built for AI agents. Listings link out to employer ATS.

## High-volume boards

- **EchoJobs** (https://echojobs.io) - 60,000+ software engineering jobs aggregated from company career pages, updated hourly. Backend-engineer filter, no reposts, no ghost jobs. Most applications land on Greenhouse/Ashby/Lever.
- **Remotive** (https://remotive.com) - 140,000+ remote listings. Category, seniority, salary, and timezone filters. Mostly redirects to employer sites.
- **Startup Jobs** (https://startup.jobs) - Startup board with AI/engineering categories, workplace and experience-level filters.
- **Indeed** (https://indeed.com) - Largest general volume. Apply via Indeed's flow or follow through to the employer site when it redirects.

## Niche and high-signal boards

- **Kube Careers** (https://kube.careers) - Hand-picked Kubernetes and infrastructure jobs with salary ranges and tech-stack tags. Small volume, highest signal for infra roles.
- **LeadJobs.dev** (https://leadjobs.dev) - Staff+ and leadership roles only (Staff, EM, Director, VP, CTO).
- **Hacker News Who's Hiring** (https://news.ycombinator.com/submitted?id=whoishiring) - Monthly thread with direct apply links in comments. High freshness for startups; prioritize the first 10 days of each month.
- **whoishiring.io** - Searchable aggregator for the HN Who's Hiring threads.
- **golangprojects.com** - Go-specific jobs since 2014, with a remote section.

## AI and ML specific

- **aimljobs.fyi** - Top AI companies and startups, updated daily.
- **explorejobs.ai** - Engineering, product, and research roles at AI startups.
- **moaijobs.com** - AI/ML/data/engineering/research board.
- **agentic-engineering-jobs.com** - Niche board for RAG, AI-agent, and LLM-product engineers.
- **aijobs.18offers.com** - Live aggregator of 400+ AI-first company job boards, refreshed daily.
- **ml-jobs.ai** - Curated ML/AI postings.
- **aijobs.ai** - Thousands of AI/ML/data roles.
- **ai-jobs.net** - AI jobs board.
- **Hugging Face Jobs** - ML-community board, direct line into ML-ecosystem companies.

## Remote-first boards

- **dailyremote.com** - Remote board with salary-first browsing.
- **dynamitejobs.com** - Curated remote-first company jobs.
- **justremote.co** - Remote board with category filters.
- **jobgether.com** - Flexible-work board.
- **RemoteOK** (https://remoteok.com) - Remote jobs with a public API for discovery.
- **We Work Remotely** (https://weworkremotely.com) - Free tier discovery, follow ATS links out.
- **Working Nomads** (https://www.workingnomads.com) - Remote jobs list.
- **Himalayas** (https://himalayas.app) - Remote jobs.
- **Jobspresso** (https://jobspresso.co) - Remote jobs.
- **Remote.co** (https://remote.co) - Remote jobs.
- **NoDesk** (https://nodesk.co) - Remote jobs.
- **4dayweek.io** - Companies with 4-day weeks.

## Startup and VC portfolio boards

- **Wellfound** (https://wellfound.com) - Startup jobs. Has anti-bot protection (DataDome); run headed.
- **YC Jobs** (https://www.ycombinator.com/jobs) - Y Combinator company jobs.
- **a16z portfolio** (https://jobs.a16z.com) - Andreessen Horowitz portfolio jobs.
- **Other VC boards**: jobs.accel.com, jobs.greylock.com/jobs, jobs.khoslaventures.com, jobs.sequoiacap.com/jobs, jobs.nea.com, jobs.generalcatalyst.com, portfoliojobs.tcv.com, and similar. Follow Ashby/Lever/Greenhouse links out.

## General and supplemental

- **Dice** (https://www.dice.com) - Tech-focused job board.
- **Built In** (https://builtin.com) - Tech jobs by city.
- **Levels.fyi Jobs** - Filter by verified compensation, follow ATS links.
- **TrueUp** (https://trueup.io) - Salary-transparent tech jobs.
- **Authentic Jobs** (https://authenticjobs.com) - On-page apply forms.
- **The Muse** (https://www.themuse.com) - Company profiles with job listings.
- **Tech:NYC** - NYC tech jobs board.
- **findwork.dev** - Software engineering aggregator with a developer API.
- **grepjob.com** - Engineering jobs scraped from career pages.
- **climatetechlist.com** - 30,000+ openings across climate-tech companies.
- **techfetch.com** - US tech and IT matching.
- **powertofly.com** - Diversity-focused tech roles.

## Aggregators (discovery only, evaluate before automating)

These aggregate well but may need accounts or have mixed automation reviews. Use for discovery, apply on the employer's own site.

- **Simplify** (https://simplify.jobs) - Indexes 50+ job boards and career pages daily. Do not use their auto-apply feature.
- **Jobright** (https://jobright.ai) - AI-matched jobs from ATS crawls. Account and resume required.

## Company boards (direct)

For target companies, go straight to their ATS board. ai-job-pilot has proven fill templates for these.

- Ashby: `jobs.ashbyhq.com/<company>`
- Lever: `jobs.lever.co/<company>`
- Greenhouse: `job-boards.greenhouse.io/<company>`
- Workable: `apply.workable.com/<company>`

## Tips

- Always verify title, location, salary, and sponsorship language on the employer's own posting before applying.
- Deduplicate against your applied list before each run.
- Prefer API and structured sources over DOM scraping when available.
- Pace requests conservatively on any single site.
