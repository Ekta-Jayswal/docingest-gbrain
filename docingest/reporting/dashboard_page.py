# What this file does: builds the run-history web page (one static HTML file) from the
# run history. It shows totals, activity, evaluation scores, runs and every document's details.

"""The run-history page: one static HTML file built from the run history.

Overall totals (by project, kind, file type, status, team), activity over time, the runs,
the latest evaluation scores, and one expandable entry per ingested document showing
exactly what happened to it: input path, sections, every table and what it became,
pages by type, chunks, links by verb and each verification check. One small inline script pages the document list; no
network: the file opens straight from the work volume.
"""

from __future__ import annotations

import html
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from docingest.reporting import run_history
from docingest.utils import clock

# How many document entries the page shows in full. Older entries are left out to keep the file
# small.
MAX_DOCUMENTS = 500  # newest document entries shown in full

# Styles for the page. Colours are CSS variables, with a separate set for dark mode.
CSS = """
:root{--bg:#ecd9bd;--panel:#f6e8d0;--ink:#3a2a1c;--quiet:#7a6248;--rule:#d6bf9c;
--accent:#b8602c;--ai:#6a4fa3;--good:#4a7a35;--warn:#a8710f;--bad:#a93a28;--bar:#dcb88a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
h3{font-size:14px;margin:16px 0 6px}
.sub{color:var(--quiet);margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.card{font:inherit;color:inherit;text-align:left;cursor:pointer;background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:12px}
.card{position:relative;transition:transform .12s,box-shadow .12s,border-color .12s}
.card:hover{transform:translateY(-2px);border-color:var(--accent);box-shadow:0 3px 8px rgba(120,70,20,.18)}
.card:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.card .more{display:block;margin-top:6px;color:var(--accent);font-size:12px;font-weight:600}
.card .more:after{content:" \\25BE"}.card.on .more:after{content:" \\25B4"}
.card.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.card b{display:block;font-size:22px}.card span{color:var(--quiet);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:14px}
.panel{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:12px}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--rule);vertical-align:top}
th{color:var(--quiet);font-weight:600;font-size:12px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.bar{background:var(--bar);height:10px;border-radius:3px;min-width:2px}
.st{font-weight:600;font-size:12px}.pass{color:var(--good)}.warn{color:var(--warn)}
.fail,.failed{color:var(--bad)}.info{color:var(--quiet)}
details{background:var(--panel);border:1px solid var(--rule);border-radius:8px;margin:8px 0}
summary{cursor:pointer;padding:10px 12px;display:flex;gap:12px;flex-wrap:wrap}
summary .f{font-weight:600}summary .m{color:var(--quiet)}
details>div{padding:0 12px 12px}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
.days{display:flex;align-items:flex-end;gap:6px;height:150px;padding-top:4px;overflow-x:auto}
.day{flex:1;min-width:30px;max-width:56px;height:100%;display:flex;flex-direction:column;
justify-content:flex-end;align-items:center}
.day .col{width:100%;background:var(--accent);border-radius:3px 3px 0 0}
.day .num{font-size:12px;font-weight:600}.day .dt{font-size:11px;color:var(--quiet);margin-top:3px}
.btn{display:inline-flex;gap:10px;align-items:center;background:var(--accent);color:#fff8ee;text-decoration:none;
font-weight:600;border-radius:8px;padding:8px 16px;margin:4px 0 8px}
.btn:hover{filter:brightness(1.08)}
.badge{background:#fff8ee;color:var(--quiet);border-radius:999px;padding:1px 10px;font-size:12px}
.badge.on{color:var(--bad)}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.chip{border:1px solid var(--rule);border-radius:999px;padding:3px 12px;background:var(--panel);font-size:13px}
.chip.differ,.pill.differ{border-color:var(--bad);color:var(--bad)}.chip.ai,.pill.ai{border-color:var(--ai);color:var(--ai)}
.chip.rules,.pill.rules{border-color:var(--good);color:var(--good)}.chip.agree,.pill.agree{border-color:var(--good);color:var(--good)}
.chip.none,.pill.none{color:var(--quiet)}
.pill{border:1px solid var(--rule);border-radius:999px;padding:0 10px;font-size:12px;font-weight:600;height:20px;line-height:18px}
details.rv{border-left:4px solid var(--accent)}
.sgs{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin:8px 0}
.sg{border:1px solid var(--rule);border-radius:8px;padding:10px;background:var(--bg)}
.sg.rules{border-left:4px solid var(--good)}.sg.ai{border-left:4px solid var(--ai)}.sg.off{opacity:.7}
.sg p{margin:4px 0}.sg blockquote{margin:6px 0 0;padding:6px 10px;border-left:3px solid var(--ai);background:var(--panel);font-style:italic}
.notice{background:var(--bg);border:1px solid var(--bad);border-left:4px solid var(--bad);border-radius:6px;padding:6px 10px;margin:8px 0}
details.rv code{word-break:normal;overflow-wrap:anywhere}
ul.plain{list-style:none;margin:0;padding:0}ul.plain li{margin:3px 0}
.acts{margin-top:10px}
.copy{font:inherit;font-size:12px;background:var(--panel);color:var(--accent);border:1px solid var(--accent);border-radius:6px;padding:3px 9px;margin:2px 4px 2px 0;cursor:pointer}
.copy.active{background:var(--accent);color:var(--panel)}
.cmdbox{margin-top:10px;background:var(--bg);border:1px solid var(--rule);border-radius:8px;padding:8px 14px}
.cmdbox pre{background:var(--panel);border:1px solid var(--rule);border-radius:6px;padding:8px;overflow-x:auto;margin:6px 0}
.cmdbox ol{margin:6px 0 4px;padding-left:20px}
.copy:hover{background:var(--accent);color:var(--panel)}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.filters input,.filters select{font:inherit;background:var(--panel);color:var(--ink);
border:1px solid var(--rule);border-radius:6px;padding:5px 8px}
.filters input{flex:1;min-width:220px}
.runs{width:max-content}
.runs th,.runs td{white-space:nowrap;padding:7px 14px}
.runs code{word-break:normal;white-space:nowrap}
.runs tr:hover td{background:rgba(184,96,44,.08)}
.muted{color:var(--quiet)}
.pager{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0}
.pager button,.pager select{font:inherit;background:var(--panel);color:var(--ink);
border:1px solid var(--rule);border-radius:6px;padding:4px 10px;cursor:pointer}
.filters button{font:inherit;background:var(--panel);color:var(--ink);border:1px solid var(--rule);border-radius:6px;padding:5px 10px;cursor:pointer}
.pager button:disabled{opacity:.45;cursor:default}
.llm{color:var(--accent);font-weight:600}
"""


# Summary cards: recomputed in the browser for the chosen date range (the newest record of each
# document within the range). Clicking a card lists what makes up its number.
SUMMARY_JS = """
(function(){var D=JSON.parse(document.getElementById('summary-data').textContent);
var from=document.getElementById('s-from'),to=document.getElementById('s-to'),
box=document.getElementById('cards'),det=document.getElementById('card-detail'),open=null;
function esc(v){var e=document.createElement('span');e.textContent=v==null?'':v;return e.innerHTML.replace(/"/g,'&quot;')}
function inRange(d){return (!from.value||d>=from.value)&&(!to.value||d<=to.value)}
function table(head,rows){return '<table><tr>'+head.map(function(h){return '<th>'+h+'</th>'}).join('')+'</tr>'+
rows.map(function(r){return '<tr>'+r.map(function(c){return '<td>'+esc(c)+'</td>'}).join('')+'</tr>'}).join('')+'</table>'}
function draw(){var latest={};D.docs.filter(function(d){return inRange(d.date)}).forEach(function(d){latest[d.project+'|'+d.source]=d});
var docs=Object.keys(latest).map(function(k){return latest[k]}),ok=docs.filter(function(d){return d.status!=='failed'}),
failed=docs.filter(function(d){return d.status==='failed'}),runs=D.runs.filter(function(r){return inRange(r.date)});
function sum(k){return ok.reduce(function(a,d){return a+d[k]},0)}
function by(k){return ok.filter(function(d){return d[k]>0}).sort(function(a,b){return b[k]-a[k]}).map(function(d){return [d.file,d.project,d[k]]})}
var projects={};docs.forEach(function(d){projects[d.project]=(projects[d.project]||0)+1});
var cards=[
['Documents',docs.length,'distinct input files, latest state',function(){return table(['File','Project','Date','Result'],docs.map(function(d){return [d.file,d.project,d.date,d.status]}))}],
['Runs',runs.length,'ingestion runs recorded',function(){return table(['Run','Date','Project','Docs','Failed'],runs.map(function(r){return [r.id,r.date,r.project,r.documents,r.failed]}))}],
['Projects',Object.keys(projects).length,'with at least one document',function(){return table(['Project','Documents'],Object.keys(projects).sort().map(function(k){return [k,projects[k]]}))}],
['Failed now',failed.length,'latest attempt failed',function(){return failed.length?table(['File','Project','Date','Error'],failed.map(function(d){return [d.file,d.project,d.date,d.error]})):'Nothing failed.'}]];
[['Tables','tables','found in documents'],['Entities','entities','typed entity pages'],['Chunks','chunks','stored in the brain'],['Links','links','typed links written']].forEach(function(m){
cards.push([m[0],sum(m[1]),m[2],function(){var r=by(m[1]);return r.length?table(['File','Project',m[0]],r):'None.'}])});
var seen={},verbs={};ok.forEach(function(d){(d.linkIds||[]).forEach(function(i){if(!seen[i]){seen[i]=1;var v=D.linkTable[i].split('|')[1];verbs[v]=(verbs[v]||0)+1}})});
var uniq=Object.keys(seen).length;
cards.push(['Unique links',uniq,'distinct source, verb and target',function(){return uniq?'<p class="muted">'+sum('links')+' typed links written, '+uniq+' unique (shared entities are linked from several documents).</p>'+table(['Verb','Unique links'],Object.keys(verbs).sort(function(a,b){return verbs[b]-verbs[a]}).map(function(v){return [v,verbs[v]]})):'No link detail recorded yet. Runs recorded from now on include it.'}]);
box.innerHTML='';
cards.forEach(function(c){var b=document.createElement('button');b.className='card'+(open===c[0]?' on':'');
b.innerHTML='<b>'+esc(c[1])+'</b>'+esc(c[0])+'<br><span>'+esc(c[2])+'</span><span class="more">'+(open===c[0]?'Hide details':'View details')+'</span>';
b.onclick=function(){open=open===c[0]?null:c[0];draw()};box.appendChild(b);
if(open===c[0]){det.hidden=false;det.innerHTML='<h3>'+esc(c[0])+'</h3>'+c[3]()}});
if(open===null)det.hidden=true}
from.onchange=to.onchange=draw;
document.getElementById('s-reset').onclick=function(){from.value=to.value='';draw()};
draw()})();
"""


# Filters and paging for the document list. The search box matches any text in an entry, even
# in collapsed ones; the dropdowns match exact values; paging applies to the filtered list.
PAGER_JS = """
(function(){var box=document.getElementById('docs');if(!box)return;
var items=[].slice.call(box.children).filter(function(e){return e.tagName==='DETAILS'});
if(!items.length)return;var size=10,page=0,hits=items;
var bars=[document.getElementById('pager'),document.getElementById('pager2')];
var q=document.getElementById('f-q'),count=document.getElementById('f-count');
var fields=['status','project','kind','ext','team','llm','run'];
var sel={};fields.forEach(function(f){sel[f]=document.getElementById('f-'+f);
var vals={};items.forEach(function(e){vals[e.dataset[f]||'']=1});
Object.keys(vals).sort().forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v||'-';sel[f].appendChild(o)});
sel[f].onchange=function(){page=0;show()}});
var text=items.map(function(e){return e.textContent.toLowerCase()});
q.oninput=function(){page=0;show()};
document.getElementById('f-reset').onclick=function(){q.value='';fields.forEach(function(f){sel[f].value=''});page=0;show()};
function show(){var t=q.value.toLowerCase().trim();
hits=items.filter(function(e,i){if(t&&text[i].indexOf(t)<0)return false;
return fields.every(function(f){return !sel[f].value||e.dataset[f]===sel[f].value})});
var all=size===0,pages=all?1:Math.max(1,Math.ceil(hits.length/size));
if(page>=pages)page=pages-1;
items.forEach(function(e){e.style.display='none'});
hits.forEach(function(e,i){if(all||Math.floor(i/size)===page)e.style.display=''});
count.textContent=hits.length+' of '+items.length+' documents match';
bars.forEach(function(b){b.innerHTML='';
function btn(t,d,f){var x=document.createElement('button');x.textContent=t;x.disabled=d;x.onclick=f;b.appendChild(x)}
btn('Prev',page===0||all,function(){page--;show()});
var l=document.createElement('span');l.textContent=all?hits.length+' shown (all)':'Page '+(page+1)+' of '+pages;b.appendChild(l);
btn('Next',all||page>=pages-1,function(){page++;show()});
var s=document.createElement('select');[10,25,50,0].forEach(function(n){var o=document.createElement('option');o.value=n;o.textContent=n?n+' per page':'All';if(n===size)o.selected=true;s.appendChild(o)});
s.onchange=function(){size=+s.value;page=0;show()};b.appendChild(s)})}
show()})();
"""


# The review queue, read from <work>/review. Read-only: it never changes those files. Anything
# unreadable gives an empty queue, so a bad review file cannot break the page.
def _review_data(work: Path) -> dict:
    try:
        from docingest.ingestion.review_proposals import ReviewBoard, decision_sig

        board = ReviewBoard(work)
        open_ = []
        for pid, p in sorted(board.pending().items()):
            rules, ai = p.get("rules_suggestion") or {}, p.get("ai_suggestion")
            has_rules = rules.get("action") not in (None, "none")
            # Which suggestions exist, and whether they agree, decides the group on the page.
            if ai and has_rules:
                group = "agree" if decision_sig(ai) == decision_sig(rules) else "differ"
            else:
                group = "ai" if ai else "rules" if has_rules else "none"
            sug = p.get("suggestion") or {}
            alts = p.get("alternatives") or []
            open_.append(
                {
                    "id": pid,
                    "kind": str(p.get("kind", "")).replace("_", " "),
                    "found": p.get("found"),
                    "group": group,
                    "rules": _sug(board, rules) if rules else None,
                    "ai": _sug(board, ai) if ai else None,
                    "canApprove": sug.get("action") not in (None, "none"),
                    "mapTo": alts[0]["name"] if alts else None,
                    "alternatives": [
                        {"name": x.get("name"), "description": x.get("description")} for x in alts
                    ],
                    "checks": [
                        {"name": c.get("name"), "ok": bool(c.get("ok")), "detail": c.get("detail")}
                        for c in p.get("checks", [])
                    ],
                    "whyNotAutomatic": p.get("why_not_automatic"),
                    "documents": p.get("documents", []),
                    "rows": len(p.get("rows", [])),
                    "sample": [_sample_row(p, r) for r in p.get("rows", [])[:3]],
                    "approvals": [a.get("by") for a in p.get("approvals", [])],
                    # Why the last attempt to apply it was refused or rolled back, if it was.
                    "refused": p.get("refused"),
                    # What a recorded decision still needs before it can be applied.
                    "waitingOn": _waiting_on(board, p),
                }
            )
        recent = [
            {
                "date": e["at"][:10],
                "event": e["event"],
                "proposal": e.get("proposal"),
                "by": e.get("by"),
                "reason": e.get("reason"),
            }
            for e in board.recent()
        ]
        return {"open": open_, "recent": recent}
    except Exception:
        return {"open": [], "recent": []}


# The review page is written next to the dashboard, under this name.
REVIEW_FILE = "review.html"


# What a recorded decision still needs before it can be applied (for example a second reviewer).
# None when nobody has decided yet, or when the backend cannot say.
def _waiting_on(board, p: dict) -> str | None:
    if not p.get("approvals"):
        return None
    try:
        return board.ready(p, board.vocab())[1] or None
    except Exception:
        return None


# One suggestion, as the page shows it: what it does, who made it, why, and the quoted evidence.
def _sug(board, s: dict) -> dict:
    return {
        "does": board._describe(s) if s.get("action") else "",
        "by": s.get("by"),
        "reason": s.get("reason"),
        "evidence": s.get("evidence"),
        "none": s.get("action") in (None, "none"),
    }


# One affected row, as a line of text, for the sample under a proposal.
def _sample_row(p: dict, r: dict) -> str:
    if "cells" in r:
        return f"[{r.get('doc')}] " + " | ".join(f"{k}: {v}" for k, v in r["cells"].items())
    targets = ", ".join(f"{t} ({d})" for t, d in r.get("targets", []))
    return f"[{r.get('doc')}] {r.get('id')} ({r.get('type')}): \"{r.get('cell')}\" -> {targets}"


# The review section at the top of the page: counts, then the open proposals in groups, each with
# what the rules and the AI suggested, the checks, the rows affected and buttons that copy the
# review command.
REVIEW_GROUPS = [
    ("differ", "Rules and AI disagree", "Read these first: the two suggestions point different ways."),
    ("ai", "AI suggestion only", "The rules had no clear answer. The AI proposed one, with a quote as evidence."),
    ("rules", "Rules suggestion only", "The rules have an answer. No AI suggestion was asked for or accepted."),
    ("agree", "Rules and AI agree", "Both reached the same decision."),
    ("none", "No suggestion", "Nobody could place it. Leaving it generic is safe."),
]


def _suggestion_box(title: str, s: dict | None, cls: str) -> str:
    if not s:
        return f'<div class="sg off"><b>{_e(title)}</b><p class="muted">No suggestion.</p></div>'
    if s["none"]:
        body = f'<p>Leave generic.</p><p class="muted">{_e(s.get("reason"))}</p>'
    else:
        body = f'<p><b>{_e(s["does"])}</b></p><p class="muted">{_e(s.get("reason"))}</p>'
        if s.get("evidence"):
            body += f'<blockquote>{_e(s["evidence"])}</blockquote>'
    return f'<div class="sg {cls}"><b>{_e(title)}</b>{body}</div>'


def _proposal(p: dict) -> str:
    checks = "".join(
        f"<li class='{'pass' if c['ok'] else 'fail'}'>{'&#10003;' if c['ok'] else '&#10007;'} {_e(c['name'])}"
        + ("" if c["ok"] else f" <span class='muted'>({_e(c['detail'])})</span>")
        + "</li>"
        for c in p["checks"]
    )
    alts = "".join(
        f"<li><b>{_e(a['name'])}</b> <span class='muted'>{_e(a['description'])}</span></li>"
        for a in p["alternatives"]
    )
    sample = "".join(f"<li><code>{_e(x)}</code></li>" for x in p["sample"])
    by = ' --by {by} --note "..."'
    cmds = [("Show details", f"./ingest.sh review show {p['id']}")]
    if p["canApprove"]:
        cmds.append(("Approve suggestion", f"./ingest.sh review approve {p['id']}{by}"))
    target = p["mapTo"] or "<type-or-verb>"
    cmds.append((f"Map to {p['mapTo']}" if p["mapTo"] else "Map to existing", f"./ingest.sh review map {p['id']} {target}{by}"))
    cmds.append(("Reject (keep generic)", f"./ingest.sh review reject {p['id']}{by}"))
    buttons = "".join(
        f'<button class="copy" data-cmd="{_e(c)}" data-kind="{"read" if " show " in c else "write"}">{_e(t)}</button>'
        for t, c in cmds
    )
    approvals = ", ".join(p["approvals"]) if p["approvals"] else "none yet"
    return (
        f"<details class='rv' open><summary><code>{_e(p['id'])}</code><span class='f'>{_e(p['found'])}</span>"
        f"<span class='pill {_e(p['group'])}'>{_e(p['kind'])}</span></summary><div>"
        + (
            f"<p class='notice'><b>Sent back to review.</b> {_e(p['refused'])}</p>" if p.get("refused") else ""
        )
        + f"<div class='sgs'>{_suggestion_box('Rules suggest', p['rules'], 'rules')}"
        f"{_suggestion_box('AI suggests', p['ai'], 'ai')}</div>"
        f"<div class='grid'><div><h3>Checks</h3><ul class='plain'>{checks or '<li class=muted>none</li>'}</ul>"
        + (f"<p class='muted'>Not applied automatically: {_e(p['whyNotAutomatic'])}</p>" if p.get("whyNotAutomatic") else "")
        + f"</div><div><h3>Closest alternatives</h3><ul class='plain'>{alts or '<li class=muted>none</li>'}</ul></div>"
        f"<div><h3>Affected</h3><p>{p['rows']} row(s) in {_e(', '.join(p['documents']))}</p>"
        f"<ul class='plain'>{sample}</ul><p class='muted'>Approvals: {_e(approvals)}</p>"
        + (f"<p class='muted'>Waiting: {_e(p['waitingOn'])}</p>" if p.get("waitingOn") else "")
        + "</div></div>"
        f"<div class='acts'><span class='muted'>Copy a command:</span> {buttons}</div>"
        "<div class='cmdbox' hidden></div></div></details>"
    )


def _review_section(review: dict) -> str:
    open_, recent = review["open"], review["recent"]
    counts = Counter(p["group"] for p in open_)
    chips = "".join(
        f"<span class='chip {k}'><b>{counts[k]}</b> {_e(t)}</span>" for k, t, _ in REVIEW_GROUPS if counts[k]
    )
    head = (
        "<h2>Review</h2><p class='muted'>Schema gaps the vocabulary could not place. "
        "This page only prepares commands: a button copies one to your clipboard and changes nothing. "
        "You then run it in a terminal, where the review command checks the decision, asks you to confirm, "
        "records it and keeps the audit log. Copying again is harmless.</p>"
    )
    if not open_:
        body = "<div class='panel'><p>Nothing is waiting for a reviewer.</p></div>"
    else:
        groups = "".join(
            f"<h3>{_e(t)} <span class='muted'>({counts[k]})</span></h3><p class='muted'>{_e(d)}</p>"
            + "".join(_proposal(p) for p in open_ if p["group"] == k)
            for k, t, d in REVIEW_GROUPS
            if counts[k]
        )
        body = (
            f"<div class='chips'><span class='chip all'><b>{len(open_)}</b> waiting</span>{chips}</div>"
            "<p><label class='muted'>Your name <input id='rv-name' placeholder='used as --by'></label></p>"
            + groups
        )
    done = ""
    if recent:
        done = (
            "<h3>Decided in the last 7 days</h3><ul>"
            + "".join(
                f"<li>{_e(e['date'])} {_e(e['event'].replace('_', ' '))} <code>{_e(e['proposal'])}</code>"
                + (f" by {_e(e['by'])}" if e.get("by") else "")
                + (f": {_e(e['reason'])}" if e.get("reason") else "")
                + "</li>"
                for e in recent
            )
            + "</ul>"
        )
    return head + body + done


# The only review item on the dashboard: a button to the review page, with how many are waiting.
def _review_button(review: dict) -> str:
    n = len(review["open"])
    state = f"{n} waiting" if n else "nothing waiting"
    return (
        f'<a class="btn" href="{REVIEW_FILE}">Review <span class="badge{" on" if n else ""}">'
        f"{_e(state)}</span></a>"
    )


# Makes the copy buttons work: fills in the reviewer's name and copies the command.
REVIEW_JS = """
(function(){var n=document.getElementById('rv-name');
function esc(v){var e=document.createElement('span');e.textContent=v;return e.innerHTML}
function copy(t,done){function fb(){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();var ok=false;try{ok=document.execCommand('copy')}catch(e){}a.remove();done(ok)}
if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(t).then(function(){done(true)},fb);else fb()}
[].forEach.call(document.querySelectorAll('.copy[data-cmd]'),function(b){b.onclick=function(){
var name=(n&&n.value.trim())||'',cmd=b.getAttribute('data-cmd').split('{by}').join(name||'<your-name>'),
box=b.closest('details').querySelector('.cmdbox'),write=b.getAttribute('data-kind')==='write';
[].forEach.call(b.parentNode.querySelectorAll('.copy'),function(x){x.classList.remove('active')});b.classList.add('active');
copy(cmd,function(ok){box.hidden=false;box.innerHTML=
'<p><b>'+(ok?'Copied to your clipboard.':'Could not copy automatically, select and copy it below.')+' Nothing has changed yet.</b></p>'+
'<ol><li>Open a terminal in the project folder and paste this command:<pre>'+esc(cmd)+'</pre></li>'+
(write&&!name?'<li><b>Replace &lt;your-name&gt; with your name</b> (or type it in "Your name" at the top and click the button again), and replace "..." with a short reason.</li>':'')+
(write?'<li>It shows what the decision would change and asks you to confirm. Only then is your decision recorded.</li>'+
'<li>A recorded decision takes effect at the next ingest or with <code>./ingest.sh review apply</code>, which also checks nothing broke and sends the proposal back here if it did.</li>'+
'<li>Reload this page to see the result.</li>':'<li>This only reads: it prints the full evidence and what approving would change. It records nothing.</li>')+'</ol>'})}})})();
"""


# Show a path relative to the folder the command runs from.
def _rel(path) -> str:
    if not path:
        return ""
    try:
        return os.path.relpath(str(path))
    except ValueError:  # another drive on Windows
        return str(path)


# Escape a value for HTML, so it shows as text and cannot inject markup. None becomes empty.
def _e(value) -> str:
    return html.escape("" if value is None else str(value))


# A coloured status label (PASS, WARN, FAIL and so on) for a check or run.
def _status(s) -> str:
    s = (s or "").lower()
    return f'<span class="st {_e(s.split()[0] if s else "")}">{_e(s.upper() or "-")}</span>'


# A small table of counts, sorted largest first, with a bar scaled to the biggest count.
def _count_table(title: str, counts: Counter, label: str) -> str:
    top = counts.most_common()
    peak = max((n for _, n in top), default=1)
    rows = "".join(
        f'<tr><td>{_e(k)}</td><td class="n">{n}</td>'
        f'<td style="width:45%"><div class="bar" style="width:{100 * n / peak:.0f}%"></div></td></tr>'
        for k, n in top
    )
    return (
        f'<div class="panel"><h3>{_e(title)}</h3><table><tr><th>{_e(label)}</th>'
        f'<th class="n">Documents</th><th></th></tr>{rows or "<tr><td>-</td></tr>"}</table></div>'
    )


# Keep only the newest record of each document, so totals count each file once.
def _latest_per_document(docs: list[dict]) -> list[dict]:
    """The newest record of each document (by project and source path)."""
    latest: dict[tuple, dict] = {}
    for d in docs:
        latest[(d.get("project"), d.get("source_path"))] = d
    return list(latest.values())


# A bar chart of documents processed per day, for the last 30 active days. Each bar shows its
# count above and its date below, and the header states the totals.
def _activity(docs: list[dict]) -> str:
    per_day = Counter((d.get("recorded_at") or "")[:10] for d in docs if d.get("recorded_at"))
    if not per_day:
        return '<p class="muted">No runs yet.</p>'
    days = sorted(per_day)[-30:]
    peak = max(per_day[d] for d in days)
    shown = sum(per_day[d] for d in days)
    bars = "".join(
        f'<div class="day" title="{_e(d)}: {per_day[d]} document(s)"><span class="num">{per_day[d]}</span>'
        f'<div class="col" style="height:{max(100 * per_day[d] / peak, 3):.0f}%"></div>'
        f'<span class="dt">{_e(d[5:])}</span></div>'
        for d in days
    )
    return (
        f'<div class="panel"><p><b>{shown}</b> document record(s) over <b>{len(days)}</b> active '
        f"day(s), busiest day <b>{peak}</b>.</p>"
        f'<div class="days">{bars}</div>'
        f'<p class="muted">Documents processed per day (month-day), {_e(days[0])} to {_e(days[-1])}.</p></div>'
    )


# The evaluation table: the newest score for each answer key.
def _evals(evals: list[dict]) -> str:
    if not evals:
        return (
            '<p class="muted">No evaluations yet. Run <code>docingest eval</code> to score the '
            "pipeline against the answer keys in <code>answer_keys/</code>.</p>"
        )
    latest: dict[str, dict] = {}
    for e in evals:
        # Later rows replace earlier ones, so each answer key shows its newest evaluation.
        latest[e["answer_key"]] = e
    head = (
        "<tr><th>Answer key</th><th>Evaluated</th><th class='n'>Overall</th>"
        "<th class='n'>Sections F1</th><th class='n'>Tables</th><th class='n'>Entities F1</th>"
        "<th class='n'>Attributes</th><th class='n'>Links F1</th><th class='n'>Retrieval</th></tr>"
    )

    # Format a 0-to-1 score as a whole percentage, or "-" when there is no score.
    def pct(v):
        return "-" if v is None else f"{100 * v:.0f}%"

    rows = "".join(
        f"<tr><td>{_e(k)}</td><td>{_e(e.get('evaluated_at'))}</td>"
        f"<td class='n'><b>{pct(e.get('overall'))}</b></td>"
        f"<td class='n'>{pct(e.get('sections_f1'))}</td><td class='n'>{pct(e.get('tables_accuracy'))}</td>"
        f"<td class='n'>{pct(e.get('entities_f1'))}</td><td class='n'>{pct(e.get('attributes_accuracy'))}</td>"
        f"<td class='n'>{pct(e.get('links_f1'))}</td><td class='n'>{pct(e.get('retrieval_hit_rate'))}</td></tr>"
        for k, e in sorted(latest.items())
    )
    return f'<div class="panel scroll"><table>{head}{rows}</table></div>'


# Wording for what --llm did in one run: documents it ran on and items it resolved.
def _llm_run(v) -> str:
    return f"{v[0]} doc(s), {v[1]} resolved" if v else "not used"


# The runs table: one row per run, with its project, team, counts and verification status.
# The link to a run's log file, written relative to where the dashboard file is saved.
def _log_links(work: Path, dashboard: Path):
    """A function that turns a run's log file into an 'open' link (empty when there is no log)."""
    # Return the 'log' link for one run's log file, or nothing when the run has no log.
    def link_for(log_file: str | None) -> str:
        # No link when the file is gone (old run logs are pruned) so it never opens a dead link.
        if not log_file or not (work / log_file).is_file():
            return '<span class="muted" title="log file not found">-</span>' if log_file else ""
        rel = os.path.relpath(work / log_file, dashboard.parent)
        return f'<a href="{_e(rel)}" title="open this run\'s log">log</a>'
    return link_for


# The runs table: one row per run, with its log link, counts, checks and time taken.
def _runs(runs: list[dict], docs: list[dict], link_for) -> str:
    # What --llm did in each run, added up from its documents.
    llm: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for d in docs:
        if (d.get("llm") or {}).get("used"):
            llm[d.get("run_id")][0] += 1
            llm[d.get("run_id")][1] += d["llm"].get("accepted", 0)

    head = (
        "<tr><th>Run</th><th>Log</th><th>Started</th><th>Project</th><th>Team</th><th>By</th>"
        "<th>Input path</th><th class='n'>Docs</th><th class='n'>Failed</th>"
        "<th class='n'>Pages</th><th class='n'>Chunks</th><th class='n'>Links</th>"
        "<th>Checks</th><th>--llm</th><th>Embeddings</th><th class='n'>Deleted pages</th>"
        "<th>Finished</th><th class='n'>Seconds</th><th>Command</th></tr>"
    )
    rows = "".join(
        f"<tr><td><code>{_e(r.get('run_id'))}</code></td><td>{link_for(r.get('log_file'))}</td>"
        f"<td>{_e(r.get('started_at'))}</td>"
        f"<td>{_e(r.get('project'))}</td><td>{_e(r.get('team'))}</td><td>{_e(r.get('submitted_by'))}</td>"
        f"<td><code>{_e(_rel(r.get('input_path')))}</code></td><td class='n'>{_e(r.get('documents'))}</td>"
        f"<td class='n'>{_e(r.get('failed'))}</td><td class='n'>{_e(r.get('pages'))}</td>"
        f"<td class='n'>{_e(r.get('chunks'))}</td><td class='n'>{_e(r.get('links'))}</td>"
        f"<td>{_status(r.get('verification'))}</td>"
        f"<td>{_llm_run(llm.get(r.get('run_id')))}</td><td>{_e(r.get('embeddings') or 'not used')}</td>"
        f"<td class='n'>{_e(r.get('deleted_pages'))}</td><td>{_e(r.get('finished_at'))}</td>"
        f"<td class='n'>{_e(r.get('seconds'))}</td><td><code>{_e(r.get('command'))}</code></td></tr>"
        # The newest 200 runs, most recent first.
        for r in reversed(runs[-200:])
    )
    return f'<div class="panel scroll"><table class="runs">{head}{rows}</table></div>'


# A two-column table of label and value, used for the details of one document.
def _kv(pairs: list[tuple[str, object]]) -> str:
    return (
        "<table>"
        + "".join(f"<tr><th style='width:160px'>{_e(k)}</th><td>{v}</td></tr>" for k, v in pairs)
        + "</table>"
    )


# Short and long wording for what the --llm option did to a document (searchable text).
def _llm_short(llm: dict | None) -> str:
    if not llm or not llm.get("used"):
        return "--llm not used"
    return f"--llm used, {llm.get('accepted', 0)} resolved"


def _llm_long(llm: dict | None) -> str:
    if not llm or not llm.get("used"):
        return "--llm not used for this document"
    return (
        f"<span class='llm'>--llm used</span> ({_e(llm.get('mode'))} mode, {_e(llm.get('model'))}): "
        f"{llm.get('sections_sent', 0)} section(s) sent, {llm.get('accepted', 0)} item(s) resolved, "
        f"{llm.get('rejected', 0)} rejected, {llm.get('errors', 0)} error(s)"
    )


# One expandable entry for a document: a summary line, then its input, tables, pages, links,
# checks, sections and warnings.
def _document(d: dict, link_for) -> str:
    """One expandable entry: everything recorded about one document in one run."""
    # A failed document has no content or checks, so its entry shows only its identity and the
    # error.
    failed = d.get("status") == "failed"
    # Values the filter bar reads to show or hide this entry.
    attrs = "".join(
        f' data-{k}="{_e(v)}"'
        for k, v in {
            "status": d.get("status"),
            "project": d.get("project") or "unassigned",
            "kind": d.get("kind") or "failed",
            "ext": d.get("extension"),
            "team": d.get("team") or "-",
            "run": d.get("run_id"),
            "llm": "--llm used" if (d.get("llm") or {}).get("used") else "--llm not used",
        }.items()
    )
    summary = (
        f"<summary>{_status(d.get('status'))}<span class='f'>{_e(d.get('file_name'))}</span>"
        f"<code>{_e(d.get('run_id'))}</code>"
        f"<span class='m'>{_e(d.get('project'))} · {_e(d.get('kind') or '-')} · "
        f".{_e(d.get('extension'))} · {_e(d.get('recorded_at'))}</span>"
        + (
            ""
            if failed
            else f"<span class='m'>{_e(d.get('chunks'))} chunks · {_e(d.get('entities'))} "
            f"entities · {_e(d.get('links'))} links</span>"
            f"<span class='m'>{_llm_short(d.get('llm'))}</span>"
        )
        + "</summary>"
    )
    ident = _kv(
        [
            ("Run", f"<code>{_e(d.get('run_id'))}</code> {link_for(d.get('log_file'))}"),
            ("Project / team", f"{_e(d.get('project'))} / {_e(d.get('team') or '-')}"),
            ("Submitted by", _e(d.get("submitted_by"))),
            ("Input file", f"<code>{_e(_rel(d.get('source_path')))}</code>"),
            ("Size", f"{_e(d.get('size_bytes'))} bytes"),
            ("SHA-256", f"<code>{_e(d.get('sha256'))}</code>"),
            ("Attachment of", _e(d.get("attachment_of") or "-")),
            ("Document key", f"<code>{_e(d.get('key'))}</code>"),
            ("Title", _e(d.get("title"))),
            ("Converter", _e(d.get("converter"))),
            ("--llm option", _llm_long(d.get("llm"))),
            ("Seconds", _e(d.get("seconds"))),
        ]
    )
    if failed:
        return f"<details{attrs}>{summary}<div>{ident}<h3>Error</h3><p class='fail'>{_e(d.get('error'))}</p></div></details>"
    tables = "".join(
        f"<tr><td class='n'>{_e(t.get('section'))}</td><td>{_e(', '.join(t.get('columns', [])))}</td>"
        f"<td class='n'>{_e(t.get('rows'))}</td><td>{_e(t.get('became'))}</td></tr>"
        for t in d.get("tables", [])
    )
    pages = "".join(
        f"<tr><td>{_e(k)}</td><td class='n'>{n}</td></tr>"
        for k, n in sorted(d.get("pages_by_type", {}).items(), key=lambda x: -x[1])
    )
    links = "".join(
        f"<tr><td>{_e(k)}</td><td class='n'>{n}</td></tr>"
        for k, n in sorted(d.get("links_by_verb", {}).items(), key=lambda x: -x[1])
    )
    checks = "".join(
        f"<tr><td>{_e(c['name'])}</td><td>{_status(c['status'])}</td><td>{_e(c['detail'])}</td></tr>"
        for c in d.get("checks", [])
    )
    sections = "".join(f"<li>{_e(s)}</li>" for s in d.get("sections", []))
    warnings = "".join(f"<li>{_e(w)}</li>" for w in d.get("warnings", []))
    return (
        f"<details{attrs}>{summary}<div>{ident}"
        f"<h3>Tables ({len(d.get('tables', []))})</h3><div class='scroll'><table><tr><th class='n'>Section</th>"
        f"<th>Columns</th><th class='n'>Rows</th><th>Became</th></tr>{tables}</table></div>"
        f"<div class='grid'><div><h3>Pages by type ({_e(d.get('pages'))})</h3><table>{pages}</table></div>"
        f"<div><h3>Links by verb ({_e(d.get('links'))})</h3><table>{links}</table></div></div>"
        f"<h3>Verification checks</h3><div class='scroll'><table>{checks}</table></div>"
        f"<h3>Sections ({len(d.get('sections', []))})</h3><ol>{sections}</ol>"
        + (f"<h3>Warnings</h3><ul>{warnings}</ul>" if warnings else "")
        + "</div></details>"
    )


# The distinct links of one run record. Records written before link details were recorded have
# none, so their manifest is read instead, to count unique links across every document.
def _link_keys(work: Path, record: dict) -> list[str]:
    """Distinct "source|verb|target" keys for a run record, from the record or its manifest."""
    if record.get("link_keys"):
        return record["link_keys"]
    path = work / "manifests" / f"{record.get('key')}.json"
    if not path.exists():
        return []
    m = json.loads(path.read_text())
    return sorted({f"{p['slug']}|{v}|{t}" for p in m["pages"] for v, ts in p["links"].items() for t in ts})


# Build the whole page from the run history in the work folder. Returns the HTML text.
def render(work: Path, out: Path | None = None) -> str:
    """The whole page as HTML."""
    runs = run_history.read(work, "runs")
    docs = run_history.read(work, "documents")
    evals = run_history.read(work, "evals")
    latest = _latest_per_document(docs)
    review_html = _review_button(_review_data(work))
    # The summary cards are drawn in the browser from SUMMARY_JS, so the date range can change them.
    # Distinct link keys are sent once, as a table, and each document holds indexes into it.
    link_table: dict[str, int] = {}
    summary_data = json.dumps(
        {
            "docs": [
                {
                    "project": d.get("project") or "unassigned",
                    "source": d.get("source_path"),
                    "file": d.get("file_name"),
                    "date": (d.get("recorded_at") or "")[:10],
                    "status": d.get("status"),
                    "run": d.get("run_id"),
                    "pages": d.get("pages") or 0,
                    "tables": len(d.get("tables", [])),
                    "entities": d.get("entities") or 0,
                    "chunks": d.get("chunks") or 0,
                    "links": d.get("links") or 0,
                    "error": d.get("error"),
                    "linkIds": [
                        link_table.setdefault(k, len(link_table)) for k in _link_keys(work, d)
                    ],
                }
                for d in docs
            ],
            "linkTable": list(link_table),
            "runs": [
                {
                    "id": r.get("run_id"),
                    "date": str(r.get("started_at") or "")[:10],
                    "project": r.get("project"),
                    "documents": r.get("documents"),
                    "failed": r.get("failed"),
                }
                for r in runs
            ],
        }
    ).replace("</", "<\\/")
    # Group the latest record of each document under its project.
    by_project: dict[str, list[dict]] = defaultdict(list)
    for d in latest:
        by_project[d.get("project") or "unassigned"].append(d)
    project_rows = "".join(
        f"<tr><td>{_e(p)}</td><td class='n'>{len(ds)}</td>"
        f"<td class='n'>{sum((d.get('chunks') or 0) for d in ds)}</td>"
        f"<td class='n'>{sum((d.get('links') or 0) for d in ds)}</td>"
        f"<td class='n'>{sum(d.get('status') == 'failed' for d in ds)}</td>"
        f"<td>{_e(max((d.get('recorded_at') or '') for d in ds))}</td></tr>"
        for p, ds in sorted(by_project.items())
    )
    # Four breakdown panels: by document kind, file type, result and team.
    breakdowns = "".join(
        [
            _count_table(
                "By document kind", Counter(d.get("kind") or "failed" for d in latest), "Kind"
            ),
            _count_table("By file type", Counter(d.get("extension") for d in latest), "Extension"),
            _count_table("By result", Counter(d.get("status") for d in latest), "Status"),
            _count_table("By team", Counter(d.get("team") or "-" for d in latest), "Team"),
        ]
    )
    # The newest documents, in full, most recent first.
    link_for = _log_links(work, out or work / "reports" / "dashboard.html")
    doc_entries = "".join(_document(d, link_for) for d in reversed(docs[-MAX_DOCUMENTS:]))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DocIngest Dashboard</title><style>{CSS}</style></head>
<body><main>
<h1>DocIngest Dashboard</h1>
<p class="sub">Updated {_e(clock.now().strftime("%d %b %Y, %H:%M IST"))}</p>
{review_html}
<h2>Summary</h2>
<div class="filters"><label class="muted">From <input type="date" id="s-from"></label>
<label class="muted">To <input type="date" id="s-to"></label>
<button id="s-reset">All dates</button><span class="muted">Each document counts once, at its latest run. Click a card for details.</span></div>
<div class="cards" id="cards"></div>
<div class="panel scroll" id="card-detail" hidden></div>
<h2>Projects</h2>
<div class="panel scroll"><table><tr><th>Project</th><th class="n">Documents</th>
<th class="n">Chunks</th><th class="n">Links</th><th class="n">Failed</th><th>Last activity</th></tr>
{project_rows or '<tr><td class="muted">No documents yet.</td></tr>'}</table></div>
<h2>Breakdown</h2><div class="grid">{breakdowns}</div>
<h2>Activity</h2>{_activity(docs)}
<h2>Evaluation</h2>{_evals(evals)}
<h2>Runs</h2>{_runs(runs, docs, link_for)}
<h2>Documents</h2>
<p class="muted">Newest first, one entry per document per run (up to {MAX_DOCUMENTS}).
Filter or search below, then open an entry to see its input, tables, pages, links and checks.</p>
<div class="filters"><input id="f-q" type="search" placeholder="Search documents: file name, run ID, entity, check, section ...">
<select id="f-status"><option value="">All results</option></select>
<select id="f-project"><option value="">All projects</option></select>
<select id="f-kind"><option value="">All kinds</option></select>
<select id="f-ext"><option value="">All file types</option></select>
<select id="f-team"><option value="">All teams</option></select>
<select id="f-llm"><option value="">--llm: any</option></select>
<select id="f-run"><option value="">All runs</option></select>
<button id="f-reset" class="pager-btn">Reset</button><span class="muted" id="f-count"></span></div>
<div class="pager" id="pager"></div>
<div id="docs">{doc_entries or '<p class="muted">No documents yet.</p>'}</div>
<div class="pager" id="pager2"></div>
</main>
<script id="summary-data" type="application/json">{summary_data}</script>
<script>{SUMMARY_JS}</script>
<script>{PAGER_JS}</script></body></html>
"""


# The review page: every open proposal in full, with the rules' and the AI's suggestions.
def render_review(work: Path) -> str:
    """The review page as HTML."""
    body = _review_section(_review_data(work)).replace("<h2>Review</h2>", "", 1)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review - DocIngest</title><style>{CSS}</style></head>
<body><main>
<p><a href="dashboard.html">&larr; Back to the dashboard</a></p>
<h1>Review</h1>
<p class="sub">Updated {_e(clock.now().strftime("%d %b %Y, %H:%M IST"))}</p>
{body}
</main>
<script>{REVIEW_JS}</script>
</body></html>
"""


# Write the dashboard and its review page side by side. Returns the dashboard path.
def write(work: Path, out: Path) -> Path:
    """Write the dashboard to ``out`` and the review page next to it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(work, out), encoding="utf-8")
    page = render_review(work).replace('href="dashboard.html"', f'href="{_e(out.name)}"')
    (out.parent / REVIEW_FILE).write_text(page, encoding="utf-8")
    return out
