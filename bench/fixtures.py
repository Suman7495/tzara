"""What the agent finds on the screen and in Notion: realistic, and with answers that can be checked."""


class Fixtures:
    """Texts the tasks share. The user is Sam Lee, founder of Telekinesis AI in Berlin."""

    INVOICE_THREAD = """Re: Invoice 4471 - extension request
4 messages

From: Anna Weber <anna.weber@acme-billing.com>
To: Sam Lee
Cc: Tom Becker
Date: Mon 24 Nov 2025, 09:12
Hi Sam, invoice 4471 for 4,200 EUR is due this Friday, 28 November. Please confirm payment this week.

From: Sam Lee
To: Anna Weber
Date: Mon 24 Nov 2025, 11:40
Hi Anna, could we get an extension to 5 December? Our budget approval is still pending.

From: Anna Weber
To: Sam Lee
Cc: Tom Becker
Date: Tue 25 Nov 2025, 08:55
Hi Sam, a full extension is difficult. What I can do: you pay 2,000 EUR now and the remaining 2,200 EUR by 12 December. Could you also pick a slot for a call about the 2026 contract: Tuesday 2 December at 10:00 or Wednesday 3 December at 14:00? Please let me know both by Thursday.

From: Tom Becker
To: Sam Lee, Anna Weber
Date: Tue 25 Nov 2025, 10:30
Sam, FYI finance says our budget approval comes through on 1 December, so paying 2,000 now is not possible. We can pay in full on 2 December. I am free on Wednesday 3 December in the afternoon."""

    INVOICE_SCREEN = {"app": "Microsoft Outlook", "window": "Re: Invoice 4471 - extension request"}

    DRAFT_REPLY = """Hi Anna,

Thank you for the proposal, and sorry for the delay in getting back to you. After speaking with Tom and finance, we are not able to split the payment, because our budget approval only comes through on 1 December.

We can pay the full 4,200 EUR on 2 December, and we would be glad to do the contract call on Wednesday 3 December at 14:00 if that still works for you.

Please let me know if that is acceptable, and thank you again for being flexible with us.

Kind regards"""

    CASUAL_DRAFT = """Hey Anna,

so finance says no to splitting the payment lol, budget stuff only lands on 1 Dec. we can pay everything on 2 Dec though!! and Wed 3 Dec at 14:00 works for the call, gonna block it.

cheers"""

    TEAMS_CHAT = """Project Falcon - launch prep
Priya Nair 09:02: Morning all. Reminder: the launch review is Thursday at 15:00. Sam, can you send the updated deck to the group by Wednesday noon?
Marco Silva 09:10: I'm blocked on the API keys for staging. Still waiting on DevOps since Monday.
Priya Nair 09:12: @Marco I'll ping Lena today. Sam, I also need your sign-off on the pricing slide.
Lena Fischer 09:30: Keys issued, Marco please check your inbox. FYI I'm out on Friday.
Marco Silva 09:41: Got them, thanks! Unblocked.
Sam Lee 09:50: Will do on the deck. Is the Berlin offsite still on 12 December?
Priya Nair 09:52: Yes, 12 December. Confirmed so far: Priya, Marco, Lena and Jonas. Sam is TBD.
Jonas Weber 10:05: Can someone share the venue address? Also I'd like to move the Thursday review to 16:00 if possible.
Priya Nair 10:07: 16:00 works for me. Sam?"""

    TEAMS_SCREEN = {
        "app": "Microsoft Teams",
        "window": "Project Falcon - launch prep | Microsoft Teams",
    }

    LINKEDIN_PROFILE = """Sam Lee
Founder & CTO at Telekinesis AI - Local-first AI agents
Berlin, Germany - 2,314 followers - 500+ connections

About
I build local-first AI agents that run on your own machine, so your data stays yours. Before Telekinesis I led machine-learning teams at two Berlin startups and spent six years in robotics research at TU Darmstadt. I care about useful, private AI: assistants that read your screen, remember what matters, and actually finish tasks. Open to conversations with founders, investors and researchers working on agents and on-device models.

Experience
Founder & CTO - Telekinesis AI (2024 - present)
Built Tzara, a personal AI agent for the Mac. Raised a pre-seed round and shipped to 300 beta users.
Head of Machine Learning - Nimbus Labs, Berlin (2021 - 2024)
Led a team of 9 on speech and vision models and cut inference cost by 60%.
Research Engineer - TU Darmstadt, Robotics Lab (2015 - 2021)

Skills
Machine learning - Robotics - Python - Swift - Product strategy - Team leadership

Recent posts
We shipped Tzara 0.1 to our first 300 users. Three lessons from the launch..."""

    LINKEDIN_ABOUT = LINKEDIN_PROFILE.split("About\n")[1].split("\n\nExperience")[0]

    LINKEDIN_SCREEN = {
        "app": "Google Chrome",
        "window": "Sam Lee | LinkedIn",
        "address": "https://www.linkedin.com/in/samlee-ai/",
    }

    LINKEDIN_COMPOSE = {
        "app": "Google Chrome",
        "window": "Feed | LinkedIn",
        "address": "https://www.linkedin.com/feed/",
    }

    DATA_ROOM = """# Data Room - Telekinesis AI (seed round)

## Corporate
- Certificate of incorporation: uploaded
- Cap table (fully diluted): MISSING

## Financial
- Financial model v3: uploaded
- Q3 management accounts: MISSING

## Product
- Pitch deck v4: uploaded
- Product demo video: uploaded

## Legal
- Customer contracts (top 5): in review
- Term sheet draft: uploaded"""

    ACME_NOTES = """Attendees: Sam Lee, Anna Weber, Tom Becker

Discussed invoice 4471 and the 2026 contract. Anna prefers a partial payment; Tom says budget approval lands on 1 December.

Decisions: pay in full on 2 December. Contract call on Wednesday 3 December at 14:00.

To do: Sam sends the signed NDA to Anna by Friday. Tom confirms the budget with finance on 1 December. Anna sends the 2026 contract draft before the call. Sam prepares questions on the SLA terms.

Open question: does the 2026 contract include the premium support tier?"""

    ROADMAP = """Q4: ship the dashboard and fix onboarding. Q1: Windows beta. Risks: hiring and model quality."""

    NOTION_PAGES = {
        "Data Room Index": DATA_ROOM,
        "Meeting Notes - Acme sync 2025-11-24": ACME_NOTES,
        "Project Phoenix Roadmap": ROADMAP,
    }

    @staticmethod
    def notion_screen(page_id: str, title: str, slug: str) -> dict[str, str]:
        """The screen of a browser on one of the fake Notion pages."""
        return {
            "app": "Google Chrome",
            "window": title,
            "address": f"https://www.notion.so/{slug}-{page_id}",
        }
