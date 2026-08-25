#!/usr/bin/env python3
"""Mail category rules (single source): classify by sender address / display name.

Used by gmail_backup.py (new mail landing) and other tools. First matching rule wins.
Rules are intentionally generic (international domains only) — add your own
institutions, banks, and shops as needed.
"""
import re

RULES = [
    ("Academic", re.compile(
        r"nature\.com|science\.org|aps\.org|aip\.org|acs\.org|ieee\.org|elsevier\.com|"
        r"springer|wiley\.com|iop\.org|rsc\.org|arxiv\.org|researchgate\.net|orcid\.org|"
        r"clarivate|webofscience|publons|scholar\.google|mendeley|figshare|zenodo|scitation|"
        r"optica|spie\.org|academic\.oup|tandfonline|mdpi\.com|frontiersin|plos\.org|"
        r"cell\.com|pnas\.org|sciencemag|phys\.org|semanticscholar|scopus|preprints\.org|"
        r"chemrxiv|biorxiv|osapublishing|scholaralerts|aapt\.org|link\.aps|sciencenews", re.I)),
    ("Promotions", re.compile(
        r"mailchimp|list-manage|sendgrid|mailgun|klaviyo|mailjet|campaign|marketing|mktg|emktg|"
        r"newsletter|digest|substack|unsubscribe|promo|deals?\.|offers?\.|sale\.|shop\.|store\.|"
        r"rebates|voucher|coupon|grammarly|coursera|duolingo|udemy|skillshare", re.I)),
    ("Banking/Billing", re.compile(
        r"citibank|standardchartered|hsbc\.com|americanexpress|amex|paypal\.com|stripe\.com|"
        r"visa\.com|mastercard", re.I)),
    ("Shopping", re.compile(
        r"amazon\.(com|co\.uk|de)|aliexpress\.com|ebay\.com|booking\.com|agoda\.com|"
        r"skyscanner|ikea\.com|uniqlo\.com|steampowered\.com|epicgames\.com|nintendo|"
        r"playstation|blizzard\.com|xiaomi\.com|dji\.com|bestbuy\.com|target\.com|walmart\.com|"
        r"costco\.com", re.I)),
    ("Social/Notifications", re.compile(
        r"linkedin\.com|facebook\.com|facebookmail\.com|instagram\.com|twitter\.com|x\.com|"
        r"tiktok\.com|youtube\.com|meetup\.com|eventbrite|github\.com|gitlab\.com|"
        r"stackoverflow\.com|reddit\.com|discord\.com|telegram\.org|whatsapp\.com|zoom\.us|"
        r"slack\.com|notion\.so|medium\.com|quora\.com", re.I)),
    ("Personal", re.compile(
        r"gmail\.com|qq\.com|163\.com|126\.com|outlook\.com|hotmail\.com|live\.com|"
        r"icloud\.com|me\.com|yahoo\.com|foxmail\.com|sina\.com", re.I)),
]


def categorize(addr, name):
    """addr/name → category. No rule matches → "Other"."""
    low = (addr or "").lower()
    nl = (name or "").lower()
    for cat, rx in RULES:
        if rx.search(low) or rx.search(nl):
            return cat
    return "Other"
