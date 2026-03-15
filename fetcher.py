#!/usr/bin/env python3

import urllib.request
import urllib.parse
import json
import time
import os
import re
import sys
import html
from datetime import datetime

def get_label_added_date(issue_number, github_token):
    """Fetches the date the 'plenary-approved' label was added to the issue."""
    events_url = f"https://api.github.com/repos/cplusplus/papers/issues/{issue_number}/events?per_page=100"
    req = urllib.request.Request(events_url, headers={'User-Agent': 'Cpp-Tracker'})
    if github_token:
        req.add_header('Authorization', f'Bearer {github_token}')
    
    try:
        with urllib.request.urlopen(req) as response:
            events = json.loads(response.read().decode())
            # Iterate in reverse to find the most recent addition of the label
            for event in reversed(events):
                if event.get('event') == 'labeled' and event.get('label', {}).get('name') == 'plenary-approved':
                    return event.get('created_at')
    except urllib.error.HTTPError as e:
        print(f"    Warning: HTTP Error {e.code} fetching events for issue #{issue_number}")
        
    return None

def fetch_approved_papers(cached_dates):
    papers = {}
    github_token = os.environ.get('GITHUB_TOKEN')
    
    for target_std in ["C++26", "C++29"]:
        page = 1
        print(f"Fetching {target_std} data from GitHub API...")
        while True:
            # Search query: issues in target_std with plenary-approved label (open or closed)
            query = f"repo:cplusplus/papers label:{target_std} label:plenary-approved"
            url = f"https://api.github.com/search/issues?q={urllib.parse.quote(query)}&per_page=100&page={page}"
            
            req = urllib.request.Request(url, headers={'User-Agent': 'Cpp-Tracker'})
            if github_token:
                req.add_header('Authorization', f'Bearer {github_token}')
            
            try:
                with urllib.request.urlopen(req) as response:
                    data = json.loads(response.read().decode())
            except urllib.error.HTTPError as e:
                print(f"HTTP Error: {e.code}")
                if e.code == 403:
                    print("Rate limit exceeded. Exiting to prevent baseline corruption.")
                    sys.exit(1)
                break
                
            items = data.get('items', [])
            if not items:
                break
                
            for issue in items:
                labels = [lbl['name'] for lbl in issue.get('labels', [])]
                
                paper_number = issue['title'].split()[0]
                
                # Strip the leading paper number and optional revision (e.g., "P1234 " or "P1234 R1 ")
                clean_title = re.sub(r"^\S+(?:\s+R\d+)?\s+", "", issue['title'])
                clean_title = html.unescape(clean_title)
                
                # Categorize based on WG tags
                category = "Other / Uncategorized"
                if paper_number.startswith('CWG'):
                    category = "Core Defect Report"
                elif paper_number.startswith('LWG'):
                    category = "Library Defect Report"
                elif "EWG" in labels or "CWG" in labels:
                    category = "Core Language"
                elif "LEWG" in labels or "LWG" in labels:
                    category = "Standard Library"

                paper_link = f"https://wg21.link/{paper_number.lower()}"
                
                cpp_labels = [l for l in labels if l.startswith('C++')]
                lowest_cpp_label = min(cpp_labels) if cpp_labels else target_std

                # Use paper_number as key to deduplicate if a paper is tagged with both
                approval_date = None
                needs_event_fetch = False

                if paper_number in cached_dates:
                    approval_date = cached_dates[paper_number]
                    # If the cached date matches the generic close/update time, upgrade it if we have a token
                    if github_token and approval_date in (issue.get('closed_at'), issue.get('updated_at')):
                        needs_event_fetch = True
                else:
                    needs_event_fetch = True

                if needs_event_fetch:
                    label_date = get_label_added_date(issue['number'], github_token)
                    if label_date:
                        approval_date = label_date
                    else:
                        approval_date = issue.get('closed_at') or issue.get('updated_at')
                        
                    # Respect rate limits for the events API
                    if not github_token:
                        time.sleep(60)
                    else:
                        time.sleep(0.5)

                papers[paper_number] = {
                    "number": paper_number,
                    "title": clean_title,
                    "url": issue['html_url'],
                    "paper_url": paper_link,
                    "closed_at": approval_date,
                    "category": category,
                    "target": lowest_cpp_label,
                    "state": issue.get('state', 'open')
                }
                
            if len(items) < 100:
                break
                
            page += 1
            # Search API limits: 30/min authenticated, 10/min unauthenticated
            time.sleep(2 if github_token else 6)
            
    return list(papers.values())

if __name__ == "__main__":
    # (Meeting Name, Start Date YYYY-MM-DD)
    # Papers are grouped by the most recent meeting they were closed on or after.
    # Sorted from oldest to newest. This list can be updated as new meetings conclude.
    MEETINGS = [
        ("2023-02-06", "Issaquah 2023", "C++23"),
        ("2023-06-12", "Varna 2023", "C++26"),
        ("2023-11-06", "Kona 2023", "C++26"),
        ("2024-03-18", "Tokyo 2024", "C++26"),
        ("2024-06-24", "St. Louis 2024", "C++26"),
        ("2024-11-18", "Wrocław 2024", "C++26"),
        ("2025-02-10", "Hagenberg 2025", "C++26"),
        ("2025-06-16", "Sofia 2025", "C++26"),
        ("2025-11-03", "Kona 2025", "C++26"),
        ("2026-03-23", "Croydon 2026", "C++26"),
        ("2026-06-08", "Brno 2026", "C++29"),
        ("2026-11-16", "Búzios 2026", "C++29"),
    ]

    old_data = []
    cached_dates = {}
    try:
        with open('cpp_status_baseline.json', 'r') as f:
            old_data = json.load(f)
            for batch in old_data:
                for paper in batch.get('papers', []):
                    cached_dates[paper['number']] = paper['closed_at']
    except FileNotFoundError:
        pass

    all_papers = fetch_approved_papers(cached_dates)

    target_overrides = {}
    try:
        with open('curation.json', 'r') as f:
            curation_data = json.load(f)
            reverted_papers = curation_data.get('reverted', {})
            reversion_papers = curation_data.get('reversions', {})
            for paper in all_papers:
                if paper['number'] in reverted_papers:
                    paper['status'] = 'reverted'
                    paper['reverted_by'] = reverted_papers[paper['number']].get('reverted_by', '')
                    paper['note'] = reverted_papers[paper['number']].get('note', '')
                elif paper['number'] in reversion_papers:
                    paper['status'] = 'reversion'
                    paper['note'] = reversion_papers[paper['number']].get('note', '')
    except FileNotFoundError:
        pass

    # Create a dictionary to hold the papers for each batch
    batches = {
        name: {
            "meeting_name": name,
            "meeting_date": datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m"),
            "papers": []
        } for date_str, name, _ in MEETINGS
    }
    unassigned_papers = []

    # Convert meeting start dates to datetime objects and sort from newest to oldest
    meeting_starts = [(name, datetime.strptime(date_str, "%Y-%m-%d"), target) for date_str, name, target in MEETINGS]
    meeting_starts.sort(key=lambda x: x[1], reverse=True)

    for paper in all_papers:
        paper_date = datetime.strptime(paper['closed_at'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=None)

        assigned = False
        for name, start_date, meeting_target in meeting_starts:
            # If paper was closed on or after the meeting start date, assign it to that meeting
            if paper_date >= start_date:
                paper['target'] = meeting_target # Overwrite GitHub label with true meeting target
                batches[name]['papers'].append(paper)
                assigned = True
                break
        
        if not assigned:
            # Paper is older than the oldest meeting in the list
            unassigned_papers.append(paper)

    # Assemble the final list for JSON output, in reverse chronological order
    final_data = []

    # Add the official meeting batches in reverse chronological order (based on original MEETINGS list order)
    for _, name, _ in reversed(MEETINGS):
        batch = batches[name]
        if batch["papers"]:
            # Sort papers within the batch by their closed_at date, then number, for consistent output
            batch["papers"].sort(key=lambda p: (p['closed_at'], p['number']))
            final_data.append(batch)

    # If any papers were too old to be assigned, add them to a separate group at the end
    if unassigned_papers:
        unassigned_papers.sort(key=lambda p: (p['closed_at'], p['number']))
        final_data.append({
            "meeting_name": "Older Papers",
            "meeting_date": "",
            "papers": unassigned_papers
        })

    data_differs = old_data and (old_data != final_data)

    if data_differs:
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        backup_filename = f"cpp_status_baseline-{timestamp}.json"
        os.rename('cpp_status_baseline.json', backup_filename)
        print(f"Changes detected. Backed up previous baseline to {backup_filename}")

    with open('cpp_status_baseline.json', 'w') as f:
        json.dump(final_data, f, indent=2)
        
    print(f"Successfully saved {len(all_papers)} papers into {len(final_data)} batches to cpp_status_baseline.json")
