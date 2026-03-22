#!/usr/bin/env python3
"""
Stella Maris Academy - Local Business Donor Outreach Caller
Uses Bland AI to make automated outbound calls to La Jolla businesses
soliciting donations for the school auction/gala.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BLAND_API_KEY = os.getenv("BLAND_API_KEY")
CALLBACK_PHONE = os.getenv("CALLBACK_PHONE", "+18585551234")
SCHOOL_NAME = os.getenv("SCHOOL_NAME", "Stella Maris Academy")
EVENT_NAME = os.getenv("EVENT_NAME", "Annual Auction & Gala")

BLAND_BASE_URL = "https://api.bland.ai/v1"
HEADERS = {
    "Authorization": BLAND_API_KEY,
    "Content-Type": "application/json",
}

BUSINESSES_CSV = Path(__file__).parent / "businesses.csv"
RESULTS_CSV = Path(__file__).parent / "call_results.csv"

# The AI agent's call script / task prompt
CALL_TASK = f"""You are a friendly, professional volunteer calling on behalf of {SCHOOL_NAME},
a Catholic school in La Jolla, California. You are reaching out to local businesses
to ask if they would be willing to donate an item, gift card, or experience for the
school's {EVENT_NAME}.

IMPORTANT GUIDELINES:
- Be warm, polite, and appreciative of their time
- Introduce yourself: "Hi, my name is Sarah and I'm a volunteer with {SCHOOL_NAME} in La Jolla."
- Briefly explain the event: "We're organizing our {EVENT_NAME} to raise funds for
  our students' education, and we're reaching out to wonderful local businesses like yours
  to see if you might be interested in donating an item or gift card for our silent auction."
- If they ask what kind of donations: "We welcome anything — gift cards, merchandise,
  experiences, services, or any item you'd like to contribute. Every donation makes a
  difference and your business will be recognized at the event and in our program."
- If they say YES or show interest:
  - Thank them enthusiastically
  - Ask what they'd like to donate and the approximate value
  - Ask for the best contact person's name, email, and phone for follow-up
  - Let them know someone will follow up to arrange pickup/delivery
  - Provide callback number: {CALLBACK_PHONE}
- If they say NO or are not interested:
  - Thank them graciously for their time
  - Say "We completely understand. Thank you for being part of the La Jolla community!"
- If you reach a voicemail:
  - Leave a brief, friendly message: "Hi, this is Sarah calling from {SCHOOL_NAME}
    in La Jolla. We're reaching out to local businesses about donating to our upcoming
    {EVENT_NAME}. We'd love to include your business! Please call us back at {CALLBACK_PHONE}.
    Thank you so much!"
- Keep the conversation natural and under 2 minutes
- Do NOT be pushy or aggressive
- If they ask about tax deductions, mention that {SCHOOL_NAME} is a 501(c)(3) and
  donations are tax-deductible

At the end of the call, summarize:
1. Whether they agreed to donate (yes/no/maybe)
2. What they offered to donate (if applicable)
3. Contact person name and info (if provided)
4. Any follow-up needed
"""


def load_businesses():
    """Load business list from CSV."""
    if not BUSINESSES_CSV.exists():
        print(f"Error: {BUSINESSES_CSV} not found. Run 'python build_list.py' first.")
        sys.exit(1)

    businesses = []
    with open(BUSINESSES_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            businesses.append(row)
    return businesses


def init_results():
    """Initialize results CSV if it doesn't exist."""
    if not RESULTS_CSV.exists():
        with open(RESULTS_CSV, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "business_name", "category", "phone", "address",
                "call_id", "call_status", "call_timestamp",
                "donation_response", "donation_item", "donation_value",
                "contact_person", "contact_email", "contact_phone",
                "follow_up_needed", "follow_up_date", "notes",
                "attempts", "last_attempt",
            ])


def get_existing_results():
    """Load existing call results to avoid re-calling businesses."""
    results = {}
    if RESULTS_CSV.exists():
        with open(RESULTS_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                results[row["phone"]] = row
    return results


def make_call(phone_number, business_name):
    """Trigger an outbound call via Bland AI."""
    payload = {
        "phone_number": phone_number,
        "task": CALL_TASK,
        "voice": "maya",
        "wait_for_greeting": True,
        "record": True,
        "max_duration": 3,  # max 3 minutes
        "temperature": 0.7,
        "transfer_phone_number": CALLBACK_PHONE,
        "metadata": {
            "business_name": business_name,
            "campaign": "stella_maris_auction_2026",
        },
    }

    resp = requests.post(
        f"{BLAND_BASE_URL}/calls",
        headers=HEADERS,
        json=payload,
    )

    if resp.status_code == 200:
        data = resp.json()
        return {"success": True, "call_id": data.get("call_id", data.get("id")), "data": data}
    else:
        return {"success": False, "error": resp.text, "status_code": resp.status_code}


def get_call_details(call_id):
    """Fetch call details/transcript after completion."""
    resp = requests.get(
        f"{BLAND_BASE_URL}/calls/{call_id}",
        headers=HEADERS,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


def analyze_call_result(call_details):
    """Parse call details to extract donation response info."""
    result = {
        "donation_response": "unknown",
        "donation_item": "",
        "donation_value": "",
        "contact_person": "",
        "contact_email": "",
        "contact_phone": "",
        "follow_up_needed": "no",
        "notes": "",
    }

    if not call_details:
        result["notes"] = "Could not retrieve call details"
        return result

    status = call_details.get("status", "")
    transcript = call_details.get("concatenated_transcript", "")
    summary = call_details.get("summary", "")
    answered_by = call_details.get("answered_by", "unknown")

    if answered_by == "voicemail" or status == "voicemail":
        result["donation_response"] = "voicemail"
        result["follow_up_needed"] = "yes"
        result["notes"] = "Left voicemail - needs callback"
    elif status == "no-answer":
        result["donation_response"] = "no_answer"
        result["follow_up_needed"] = "yes"
        result["notes"] = "No answer - retry later"
    elif transcript:
        transcript_lower = transcript.lower()
        if any(w in transcript_lower for w in ["yes", "sure", "happy to", "love to", "glad to", "donate"]):
            result["donation_response"] = "interested"
            result["follow_up_needed"] = "yes"
        elif any(w in transcript_lower for w in ["no thank", "not interested", "can't", "cannot"]):
            result["donation_response"] = "declined"
        else:
            result["donation_response"] = "unclear"
            result["follow_up_needed"] = "yes"

        result["notes"] = summary or transcript[:200]

    return result


def append_result(business, call_result, analysis, attempts):
    """Append a call result row to the results CSV."""
    with open(RESULTS_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            business.get("business_name", ""),
            business.get("category", ""),
            business.get("phone", ""),
            business.get("address", ""),
            call_result.get("call_id", ""),
            call_result.get("data", {}).get("status", "initiated"),
            datetime.now().isoformat(),
            analysis["donation_response"],
            analysis["donation_item"],
            analysis["donation_value"],
            analysis["contact_person"],
            analysis["contact_email"],
            analysis["contact_phone"],
            analysis["follow_up_needed"],
            "",  # follow_up_date
            analysis["notes"],
            attempts,
            datetime.now().isoformat(),
        ])


def run_campaign(max_calls=None, delay_between=30, retry_no_answers=True):
    """Run the full outbound calling campaign."""
    if not BLAND_API_KEY:
        print("Error: BLAND_API_KEY not set in .env")
        sys.exit(1)

    businesses = load_businesses()
    init_results()
    existing = get_existing_results()

    # Filter out businesses that already responded positively or declined
    to_call = []
    for biz in businesses:
        phone = biz.get("phone", "").strip()
        if not phone:
            continue
        prev = existing.get(phone)
        if prev:
            if prev.get("donation_response") in ("interested", "declined"):
                continue
            if not retry_no_answers and prev.get("donation_response") in ("no_answer", "voicemail"):
                continue
            attempts = int(prev.get("attempts", 0))
            if attempts >= 3:
                continue
        to_call.append(biz)

    total = len(to_call)
    if max_calls:
        to_call = to_call[:max_calls]

    print(f"\n{'='*60}")
    print(f"  {SCHOOL_NAME} - Donor Outreach Campaign")
    print(f"  {EVENT_NAME}")
    print(f"{'='*60}")
    print(f"  Total businesses loaded: {len(businesses)}")
    print(f"  Already contacted (skip): {len(businesses) - total}")
    print(f"  To call this run: {len(to_call)}")
    print(f"{'='*60}\n")

    if not to_call:
        print("No businesses to call. All have been contacted or max retries reached.")
        return

    for i, biz in enumerate(to_call, 1):
        name = biz.get("business_name", "Unknown")
        phone = biz["phone"]
        prev = existing.get(phone)
        attempts = int(prev["attempts"]) + 1 if prev else 1

        print(f"[{i}/{len(to_call)}] Calling {name} at {phone} (attempt #{attempts})...")

        result = make_call(phone, name)

        if result["success"]:
            call_id = result["call_id"]
            print(f"  -> Call initiated (ID: {call_id})")
            print(f"  -> Waiting 90s for call to complete...")
            time.sleep(90)

            details = get_call_details(call_id)
            analysis = analyze_call_result(details)
            append_result(biz, result, analysis, attempts)

            print(f"  -> Result: {analysis['donation_response']}")
            if analysis["notes"]:
                print(f"  -> Notes: {analysis['notes'][:100]}")
        else:
            print(f"  -> FAILED: {result.get('error', 'Unknown error')}")
            analysis = {
                "donation_response": "call_failed",
                "donation_item": "", "donation_value": "",
                "contact_person": "", "contact_email": "", "contact_phone": "",
                "follow_up_needed": "yes",
                "notes": f"API error: {result.get('error', '')}",
            }
            append_result(biz, result, analysis, attempts)

        if i < len(to_call):
            print(f"  -> Waiting {delay_between}s before next call...\n")
            time.sleep(delay_between)

    print(f"\n{'='*60}")
    print(f"  Campaign run complete! Results saved to: {RESULTS_CSV}")
    print(f"{'='*60}\n")


def show_status():
    """Show campaign status summary."""
    if not RESULTS_CSV.exists():
        print("No results yet. Run the campaign first.")
        return

    results = get_existing_results()
    total = len(results)
    interested = sum(1 for r in results.values() if r.get("donation_response") == "interested")
    declined = sum(1 for r in results.values() if r.get("donation_response") == "declined")
    voicemail = sum(1 for r in results.values() if r.get("donation_response") == "voicemail")
    no_answer = sum(1 for r in results.values() if r.get("donation_response") == "no_answer")
    failed = sum(1 for r in results.values() if r.get("donation_response") == "call_failed")

    print(f"\n{'='*60}")
    print(f"  Campaign Status - {SCHOOL_NAME}")
    print(f"{'='*60}")
    print(f"  Total calls made:   {total}")
    print(f"  Interested:         {interested}")
    print(f"  Declined:           {declined}")
    print(f"  Voicemail left:     {voicemail}")
    print(f"  No answer:          {no_answer}")
    print(f"  Failed:             {failed}")
    print(f"  Follow-up needed:   {sum(1 for r in results.values() if r.get('follow_up_needed') == 'yes')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Stella Maris Donor Outreach Caller")
    parser.add_argument("command", choices=["call", "status", "test"],
                        help="call=run campaign, status=show results, test=single test call")
    parser.add_argument("--max", type=int, default=None, help="Max calls to make this run")
    parser.add_argument("--delay", type=int, default=30, help="Seconds between calls (default: 30)")
    parser.add_argument("--phone", type=str, help="Phone number for test call")
    parser.add_argument("--no-retry", action="store_true", help="Skip retrying voicemail/no-answer")

    args = parser.parse_args()

    if args.command == "call":
        run_campaign(max_calls=args.max, delay_between=args.delay, retry_no_answers=not args.no_retry)
    elif args.command == "status":
        show_status()
    elif args.command == "test":
        if not args.phone:
            print("Error: --phone required for test call")
            sys.exit(1)
        print(f"Making test call to {args.phone}...")
        result = make_call(args.phone, "Test Business")
        print(json.dumps(result, indent=2))
