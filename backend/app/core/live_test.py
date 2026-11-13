"""Opt-in live-test safety boundary. No provisioning/authorization occurs at import.
SQLite owns request and money reservations; no body, URL or headers are persisted.
"""
import contextvars
import hashlib
import ipaddress
import json
import math
import re
import sqlite3
import time
import uuid
import zlib
from contextlib import contextmanager, closing
from pathlib import Path

import httpx
from app.core.errors import DomainError
from app.core.model_proxy import transport as model_transport, credential_redactions, PROXY_OBSERVER

STAGES = ("ingestion", "rag", "stream", "agent")
LIMITS = {"ingestion": {"embedding": 1}, "rag": {"embedding": 1, "llm": 1},
          "stream": {"embedding": 1, "llm": 1}, "agent": {"llm": 2}}
MODELS = {"llm": "qwen-flash", "embedding": "text-embedding-v4"}
CURRENT = contextvars.ContextVar("live_test_scope", default=None)
ATTEMPT_TICKET = contextvars.ContextVar("model_attempt_ticket", default=None)

class Blocked(DomainError):
    def __init__(self, reason="SAFETY_BLOCKED"):
        super().__init__(403, "LIVE_TEST_BLOCKED", reason)

def provision(path, *, offline=True):
    """Explicit one-time setup. A surviving seal prevents recreation of a lost DB."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    seal = path.with_suffix(path.suffix + ".seal")
    if path.exists() or seal.exists():
        raise FileExistsError("Existing ledger or seal must never be overwritten")
    run_id = str(uuid.uuid4())
    with seal.open("x", encoding="utf-8") as f:
        f.write(run_id)
    # Never delete the seal on failure: incomplete initialization is fail-closed.
    with path.open("xb"):
        pass
    with closing(sqlite3.connect(path)) as c:
        c.executescript("""
        CREATE TABLE project (id TEXT PRIMARY KEY, budget_nano INTEGER NOT NULL CHECK(budget_nano=50000000000));
        CREATE TABLE run (id TEXT PRIMARY KEY, offline INTEGER, authorized INTEGER,
          state TEXT, stage INTEGER, input_rate INTEGER, output_rate INTEGER,
          embedding_rate INTEGER, verified_until REAL, evidence_hash TEXT, budget_nano INTEGER NOT NULL CHECK(budget_nano BETWEEN 1 AND 5000000000));
        CREATE TABLE requests (seq INTEGER PRIMARY KEY, stage TEXT, kind TEXT, model TEXT,
          interface TEXT, predebit INTEGER, status TEXT, http_status INTEGER,
          error_type TEXT, started REAL, elapsed_ms REAL, input_bound INTEGER,
          output_bound INTEGER, reserved_nano INTEGER, input_tokens INTEGER,
          output_tokens INTEGER, usage_status TEXT, cost_nano INTEGER, run_id TEXT NOT NULL);
        """)
        c.execute("INSERT INTO project VALUES (?,50000000000)", (run_id,))
        c.execute("INSERT INTO run VALUES (?,?,0,'READY',0,NULL,NULL,NULL,NULL,NULL,5000000000)",
                  (run_id, int(offline)))
        c.commit()
    return Ledger(path)

class Ledger:
    def __init__(self, path, run_id=None):
        self.path = Path(path).resolve()
        self.run_id = run_id

    @contextmanager
    def transaction(self):
        c = None
        try:
            seal = self.path.with_suffix(self.path.suffix + ".seal").read_text(encoding="utf-8")
            c = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            project = c.execute("SELECT * FROM project").fetchone()
            if self.run_id is None:
                if c.execute("SELECT count(*) FROM run").fetchone()[0] != 1:
                    raise Blocked("EXPLICIT_ROUND_ID_REQUIRED")
                self.run_id = c.execute("SELECT id FROM run").fetchone()[0]
            row = c.execute("SELECT * FROM run WHERE id=?", (self.run_id,)).fetchone()
            if row is None or project is None or project["id"] != seal:
                raise Blocked("LEDGER_IDENTITY_MISMATCH")
            yield c, row
            c.commit()
        except Blocked:
            if c: c.rollback()
            raise
        except Exception:
            if c: c.rollback()
            raise Blocked("LEDGER_UNAVAILABLE") from None
        finally:
            if c: c.close()

    def deny(self, c, reason):
        from app.core.runtime_safety import event
        event(c,self.run_id,'SAFETY_HALT',reason)
        c.execute("UPDATE run SET state='HALTED' WHERE id=?", (self.run_id,))
        c.commit()
        raise Blocked(reason)

    def authorize(self, *, input_rate, output_rate, embedding_rate,
                  console_evidence, token_bound_attested=False, valid_until=0):
        """Human-reviewed rates in nanoyuan/token (reference: 150,1500,500).
        Attestation must cover text-only UTF-8 bound, non-thinking output cap,
        and the <=128000 input tier. This function never calls a provider.
        """
        rates = (input_rate, output_rate, embedding_rate)
        if (not all(type(x) is int and x > 0 for x in rates)
            or not console_evidence or not token_bound_attested
            or not time.time() < valid_until <= time.time() + 86400):
            raise Blocked("PRICE_OR_TOKEN_BOUND_UNVERIFIED")
        with self.transaction() as (c, r):
            if r["state"] != "READY" or c.execute("SELECT count(*) FROM requests WHERE run_id=?", (self.run_id,)).fetchone()[0]:
                self.deny(c, "CANNOT_REAUTHORIZE_USED_RUN")
            c.execute("UPDATE run SET authorized=1,input_rate=?,output_rate=?,embedding_rate=?,"
                      "verified_until=?,evidence_hash=? WHERE id=?", (*rates, valid_until,
                      hashlib.sha256(console_evidence.encode()).hexdigest(),self.run_id))

    def record_event(self,kind,reason,seq=None):
        from app.core.runtime_safety import event
        with self.transaction() as (c,r):event(c,self.run_id,kind,reason,seq)

    def halt(self,reason='UNSPECIFIED_HALT'):
        from app.core.runtime_safety import event
        with self.transaction() as (c, _):
            event(c,self.run_id,'SAFETY_HALT',reason)
            c.execute("UPDATE run SET state='HALTED' WHERE id=?", (self.run_id,))

    def diagnostic(self, c):
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='diagnostic_grants'").fetchone()
        return c.execute("SELECT * FROM diagnostic_grants WHERE run_id=?",(self.run_id,)).fetchone() if exists else None

    def rag_grant(self, c):
        exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rag_grants'").fetchone()
        return c.execute("SELECT * FROM rag_grants WHERE run_id=?", (self.run_id,)).fetchone() if exists else None

    def development(self, c):
        from app.core.development_budget import policy
        return policy(c,self.run_id)

    def begin_development_session(self, *, grant_id, allowed_tools=(), allow_embedding=True, max_requests=100):
        from app.core.development_budget import begin
        return begin(self,grant_id,allowed_tools,allow_embedding,max_requests)

    def complete_development_session(self):
        with self.transaction() as (c,r):
            if not self.development(c) or r['state']!='READY' or c.execute("SELECT count(*) FROM requests WHERE run_id=? AND status='PENDING'",(self.run_id,)).fetchone()[0]:
                self.deny(c,'SESSION_NOT_COMPLETE')
            c.execute("UPDATE run SET state='COMPLETE' WHERE id=?",(self.run_id,))

    def begin_rag_round(self, *, grant_id):
        """Append one explicitly granted 5-request round; never rewrite old rows/seal.

        Only fully reserved, failed Embedding 403s may remain unknown. All old
        reservations stay charged against the project cap, even after success.
        """
        if not grant_id:
            raise Blocked("EXPLICIT_RAG_GRANT_REQUIRED")
        with self.transaction() as (c, r):
            if c.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]:
                raise Blocked("ACTIVE_ROUND_EXISTS")
            prior = c.execute("SELECT q.*,r.embedding_rate AS historical_rate FROM requests q JOIN run r ON q.run_id=r.id").fetchall()
            if not prior or any(x["status"] == "PENDING" for x in prior):
                raise Blocked("PENDING_OR_MISSING_HISTORY")
            for x in prior:
                if (type(x["reserved_nano"]) is not int or x["reserved_nano"] <= 0
                    or (x["cost_nano"] is not None and x["cost_nano"] > x["reserved_nano"])):
                    raise Blocked("UNBOUNDED_HISTORICAL_CHARGE")
                if x["usage_status"] == "UNKNOWN":
                    if not (x["status"] == "FAILED" and x["http_status"] == 403
                            and x["kind"] == "embedding" and x["model"] == MODELS["embedding"]
                            and type(x["historical_rate"]) is int and x["historical_rate"] >= 500
                            and x["input_bound"] >= 8192
                            and x["reserved_nano"] >= x["input_bound"] * x["historical_rate"]):
                        raise Blocked("UNBOUNDED_HISTORICAL_CHARGE")
                elif x["status"] != "SUCCESS" or x["cost_nano"] is None:
                    raise Blocked("UNRECONCILED_PRIOR_REQUEST")
            # Admission check for the entire round, not just its first request.
            worst = 3 * 8192 * 500 + 2 * (1_000_000 * 1200 + 512 * 12000)
            occupied = sum(x["reserved_nano"] for x in prior)
            if occupied + worst > c.execute("SELECT budget_nano FROM project").fetchone()[0]:
                raise Blocked("PROJECT_MONEY_LIMIT")
            c.execute("CREATE TABLE IF NOT EXISTS rag_grants (run_id TEXT PRIMARY KEY, grant_hash TEXT UNIQUE, historical_max_seq INTEGER)")
            digest = hashlib.sha256(grant_id.encode()).hexdigest()
            if c.execute("SELECT 1 FROM rag_grants WHERE grant_hash=?", (digest,)).fetchone():
                raise Blocked("RAG_GRANT_ALREADY_USED")
            run_id = str(uuid.uuid4())
            c.execute("INSERT INTO run VALUES (?,?,0,'READY',0,NULL,NULL,NULL,NULL,NULL,5000000000)", (run_id,r["offline"]))
            c.execute("INSERT INTO rag_grants VALUES (?,?,?)", (run_id,digest,max(x["seq"] for x in prior)))
        return Ledger(self.path,run_id)

    def begin_diagnostic(self, *, grant_id, console_permissions_confirmed=False):
        if not grant_id or not console_permissions_confirmed:
            raise Blocked("CONSOLE_CONFIRMATION_REQUIRED")
        with self.transaction() as (c,r):
            if r["state"] != "HALTED" or c.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]:
                raise Blocked("DIAGNOSTIC_REQUIRES_HALTED_PARENT")
            prior = c.execute("SELECT * FROM requests").fetchall()
            # Unknown old 403 charges remain UNKNOWN and fully reserved. Never unlock pending sends.
            if not prior or any(x["status"] == "PENDING" for x in prior):
                raise Blocked("PENDING_REQUEST_CANNOT_BE_DIAGNOSED")
            if any(x["usage_status"] == "UNKNOWN" and not (
                    x["status"] == "FAILED" and x["http_status"] == 403 and x["kind"] == "embedding"
                    and x["reserved_nano"] >= x["input_bound"] * r["embedding_rate"]) for x in prior):
                raise Blocked("UNBOUNDED_HISTORICAL_CHARGE")
            c.execute("CREATE TABLE IF NOT EXISTS diagnostic_grants (run_id TEXT PRIMARY KEY, grant_hash TEXT UNIQUE, parent_id TEXT, historical_max_seq INTEGER)")
            digest = hashlib.sha256(grant_id.encode()).hexdigest()
            if c.execute("SELECT 1 FROM diagnostic_grants WHERE grant_hash=?",(digest,)).fetchone():
                raise Blocked("DIAGNOSTIC_GRANT_ALREADY_USED")
            run_id = str(uuid.uuid4())
            c.execute("INSERT INTO run VALUES (?,?,0,'READY',0,NULL,NULL,NULL,NULL,NULL,1000000000)",(run_id,r["offline"]))
            c.execute("INSERT INTO diagnostic_grants VALUES (?,?,?,?)",(run_id,digest,self.run_id,max(x["seq"] for x in prior)))
        return Ledger(self.path,run_id)

    def reserve(self, stage, kind, payload, *, offline):
        with self.transaction() as (c,r):
            if self.development(c):
                from app.core.development_budget import reserve
                return reserve(self,c,r,stage,kind,payload,offline)
        # Count all JSON bytes with a deliberately generous overhead allowance.
        # Live use additionally requires explicit model-specific bound attestation.
        estimate = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) * 4 + 8192
        # For live requests, reserve the entire documented model input ceiling.
        # Never infer the paid input tier from the heuristic byte estimate.
        ib = estimate if offline else (1_000_000 if kind == "llm" else 8192)
        ob = payload.get("max_tokens", 0) if kind == "llm" else 0
        with self.transaction() as (c, r):
            if r["state"] != "READY" or not r["authorized"] or bool(r["offline"]) != offline:
                self.deny(c, "RUN_NOT_AUTHORIZED")
            if not r["verified_until"] or r["verified_until"] < time.time():
                self.deny(c, "PRICE_ATTESTATION_EXPIRED")
            if stage != STAGES[r["stage"]] or kind not in LIMITS.get(stage, {}):
                self.deny(c, "UNEXPECTED_STAGE_OR_MODEL_CALL")
            count = c.execute("SELECT count(*) FROM requests WHERE run_id=?", (self.run_id,)).fetchone()[0]
            used = c.execute("SELECT count(*) FROM requests WHERE stage=? AND kind=? AND run_id=?", (stage,kind,self.run_id)).fetchone()[0]
            diagnostic = self.diagnostic(c)
            rag_grant = self.rag_grant(c)
            if rag_grant and (stage not in STAGES[:3] or (kind == "llm" and (
                    "tools" in payload or "tool_choice" in payload or any(
                        m.get("role") == "tool" or m.get("tool_calls") for m in payload.get("messages", []) if isinstance(m, dict))))):
                self.deny(c, "RAG_ONLY_NO_TOOLS")
            if diagnostic and (stage != "ingestion" or kind != "embedding"):
                self.deny(c, "DIAGNOSTIC_EMBEDDING_ONLY")
            if count >= (1 if diagnostic else (5 if rag_grant else 7)) or used >= LIMITS[stage][kind]:
                self.deny(c, "REQUEST_LIMIT")
            # No concurrent request may start while usage/result of another is unknown.
            historical_grant = diagnostic or rag_grant
            if historical_grant:
                bad = c.execute("SELECT count(*) FROM requests WHERE status!='SUCCESS' AND seq>?",(historical_grant["historical_max_seq"],)).fetchone()[0]
            else:
                bad = c.execute("SELECT count(*) FROM requests WHERE status!='SUCCESS'").fetchone()[0]
            if bad:
                self.deny(c, "PRIOR_REQUEST_NOT_SUCCESSFUL")
            allowed = ({"model","messages","temperature","max_tokens","enable_thinking","stream","stream_options","tools","tool_choice"}
                       if kind == "llm" else {"model","input","encoding_format"})
            if set(payload) - allowed:
                self.deny(c, "UNBUDGETED_FEATURE_PARAMETER")
            if payload.get("model") != MODELS.get(kind) or estimate > 128000:
                self.deny(c, "MODEL_OR_INPUT_TIER")
            if kind == "llm" and (type(ob) is not int or not 1 <= ob <= 512 or payload.get("enable_thinking") is not False):
                self.deny(c, "OUTPUT_BOUND_UNVERIFIED")
            if stage == "stream" and kind == "llm" and payload.get("stream") is not True:
                self.deny(c, "EXPECTED_STREAM")
            if stage == "rag" and kind == "llm" and payload.get("stream") is not False:
                self.deny(c, "EXPECTED_NONSTREAM")
            if stage == "agent" and kind == "llm":
                tools = payload.get("tools", [])
                if not tools or any(t.get("function",{}).get("name") != "calculator" for t in tools):
                    self.deny(c, "CALCULATOR_ONLY")
            if kind == "llm":
                messages = payload.get("messages")
                if not isinstance(messages, list) or not messages or len(messages) > 16 or any(
                    not isinstance(m, dict) or not isinstance(m.get("content"), (str, type(None))) for m in messages):
                    self.deny(c, "TEXT_ONLY_MESSAGES_REQUIRED")
            else:
                inputs = payload.get("input")
                limit = 300 if stage == "ingestion" else 1000
                if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], str) or len(inputs[0]) > limit:
                    self.deny(c, "SINGLE_SHORT_TEXT_REQUIRED")
            rate = r["input_rate"] if kind == "llm" else r["embedding_rate"]
            if not all(type(r[k]) is int and r[k] > 0 for k in ("input_rate", "output_rate", "embedding_rate")):
                self.deny(c, "PRICE_MISSING")
            quote = ib * rate + ob * r["output_rate"]
            if not offline and kind == "llm":
                # Highest Beijing tier: input 1.2 / output 12 CNY per million.
                quote = ib * max(rate * 8,1200) + ob * max(r["output_rate"] * 8,12000)
            committed = c.execute("SELECT coalesce(sum(reserved_nano),0) FROM requests WHERE run_id=?", (self.run_id,)).fetchone()[0]
            if quote + committed > r["budget_nano"]:
                self.deny(c, "ROUND_MONEY_LIMIT")
            project_committed = c.execute("SELECT coalesce(sum(reserved_nano),0) FROM requests").fetchone()[0]
            project_cap = c.execute("SELECT budget_nano FROM project").fetchone()[0]
            if quote + project_committed > project_cap:
                self.deny(c, "PROJECT_MONEY_LIMIT")
            seq = c.execute("SELECT coalesce(max(seq),0)+1 FROM requests").fetchone()[0]
            c.execute("INSERT INTO requests VALUES (?,?,?,?,?,1,'PENDING',NULL,NULL,?,NULL,?,?,?,NULL,NULL,'UNKNOWN',NULL,?)",
                      (seq, stage, kind, MODELS[kind], "chat/completions" if kind == "llm" else "embeddings", time.time(), ib, ob, quote, self.run_id))
            return seq

    def settle(self, seq, http_status, usage=None, error=None, error_details=None,finish_reason=None):
        with self.transaction() as (c, r):
            row = c.execute("SELECT * FROM requests WHERE seq=? AND run_id=?", (seq,self.run_id)).fetchone()
            if row is None or row["status"] != "PENDING":
                self.deny(c, "INVALID_SETTLEMENT")
            inp = out = cost = None
            if isinstance(usage, dict):
                inp = usage.get("prompt_tokens", usage.get("input_tokens"))
                if inp is None and row["kind"] == "embedding": inp = usage.get("total_tokens")
                out = 0 if row["kind"] == "embedding" else usage.get("completion_tokens", usage.get("output_tokens"))
            known = all(type(x) is int and x >= 0 for x in (inp, out))
            success = error is None and http_status == 200 and known
            if known:
                rate = r["input_rate"] if row["kind"] == "llm" else r["embedding_rate"]
                output_rate = r["output_rate"]
                if not r["offline"] and row["kind"] == "llm":
                    factor = 1 if inp <= 128000 else (4 if inp <= 256000 else 8)
                    rate *= factor
                    output_rate *= factor
                cost = inp * rate + out * output_rate
                if inp > row["input_bound"] or out > row["output_bound"] or cost > row["reserved_nano"]:
                    success = False
                    error = "TOKEN_BOUND_EXCEEDED"
            c.execute("UPDATE requests SET status=?,http_status=?,error_type=?,elapsed_ms=?,input_tokens=?,output_tokens=?,usage_status=?,cost_nano=? WHERE seq=?",
                      ("SUCCESS" if success else "FAILED", http_status, error or (None if known else "USAGE_UNKNOWN"),
                       (time.time()-row["started"])*1000, inp if known else None, out if known else None,
                       "KNOWN" if known else "UNKNOWN", cost, seq))
            if error_details is not None:
                c.execute("CREATE TABLE IF NOT EXISTS response_errors (seq INTEGER PRIMARY KEY, http_status INTEGER, code TEXT, message TEXT, request_id TEXT)")
                c.execute("INSERT INTO response_errors VALUES (?,?,?,?,?)",(seq,http_status,error_details["code"],error_details["message"],error_details["request_id"]))
            from app.core.runtime_safety import event
            if finish_reason is not None:event(c,self.run_id,'MODEL_FINISH',finish_reason,seq)
            if not success:
                event(c,self.run_id,'SAFETY_HALT',error or 'REQUEST_FAILED_OR_USAGE_UNKNOWN',seq)
                c.execute("UPDATE run SET state='HALTED' WHERE id=?", (self.run_id,))
            if not success:
                c.commit()
                raise Blocked("REQUEST_FAILED_OR_USAGE_UNKNOWN")

    def advance(self, stage, verified):
        with self.transaction() as (c, r):
            if self.development(c):
                if r['state']!='READY' or not verified or c.execute("SELECT count(*) FROM requests WHERE run_id=? AND status='PENDING'",(self.run_id,)).fetchone()[0]:
                    self.deny(c,'DEVELOPMENT_VALIDATION_FAILED')
                return
            rows = c.execute("SELECT * FROM requests WHERE stage=? AND run_id=?", (stage,self.run_id)).fetchall()
            counts = {k:sum(x["kind"]==k for x in rows) for k in LIMITS[stage]}
            if (r["state"] != "READY" or STAGES[r["stage"]] != stage or not verified
                or counts != LIMITS[stage] or any(x["status"] != "SUCCESS" for x in rows)):
                self.deny(c, "STAGE_VALIDATION_FAILED")
            if stage == "agent" or self.diagnostic(c) or (self.rag_grant(c) and stage == "stream"): c.execute("UPDATE run SET state='COMPLETE' WHERE id=?", (self.run_id,))
            else: c.execute("UPDATE run SET stage=stage+1 WHERE id=?", (self.run_id,))

    def begin_round(self):
        with self.transaction() as (c,r):
            if c.execute("SELECT count(*) FROM run WHERE state='READY'").fetchone()[0]:
                raise Blocked("ACTIVE_ROUND_EXISTS")
            if c.execute("SELECT count(*) FROM requests WHERE status='PENDING' OR usage_status='UNKNOWN'").fetchone()[0]:
                raise Blocked("UNRECONCILED_PRIOR_REQUEST")
            run_id = str(uuid.uuid4())
            c.execute("INSERT INTO run VALUES (?,?,0,'READY',0,NULL,NULL,NULL,NULL,NULL,5000000000)",
                      (run_id,r["offline"]))
        return Ledger(self.path,run_id)

    def report(self):
        with self.transaction() as (c, r):
            rows = [dict(x) for x in c.execute("SELECT * FROM requests WHERE run_id=? ORDER BY seq", (self.run_id,))]
            total = 0
            errors_exist = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='response_errors'").fetchone()
            for row in rows:
                if errors_exist:
                    details=c.execute("SELECT http_status,code,message,request_id FROM response_errors WHERE seq=?",(row["seq"],)).fetchone()
                    if details:row["server_error"]=dict(details)
                total = None if total is None or row["cost_nano"] is None else total + row["cost_nano"]
                row["cumulative_cost_nano"] = total
            dev=self.development(c)
            from app.core.personal_learning import is_personal
            from app.core.development_budget import risk
            return {"state":r["state"], "used":len(rows), "max_requests":dev['max_requests'] if dev else (1 if self.diagnostic(c) else (5 if self.rag_grant(c) else 7)),
                    "manual_budget_yuan":None if dev else r["budget_nano"]/1_000_000_000, "round_id":self.run_id,
                    "project_risk_nano":risk(c), "budget_alert_30_yuan":risk(c)>=30_000_000_000,
                    "requests":rows, "cost_nano":total,
                    "reserved_nano":sum(x["reserved_nano"] for x in rows),
                    "project_budget_yuan":None if is_personal(c,self.run_id) else 50,
                    "policy_version":"personal-monitor-v2" if is_personal(c,self.run_id) else "development-v1",
                    "project_reserved_nano":c.execute("SELECT coalesce(sum(reserved_nano),0) FROM requests").fetchone()[0],
                    "project_cost_nano":None if c.execute("SELECT count(*) FROM requests WHERE cost_nano IS NULL").fetchone()[0] else c.execute("SELECT coalesce(sum(cost_nano),0) FROM requests").fetchone()[0]}

class Stage:
    def __init__(self, ledger, stage, *, offline=True, allow_application_failure=False):
        self.ledger, self.stage, self.offline = ledger, stage, offline
        self.verified = False
        self.allow_application_failure = allow_application_failure

    def __enter__(self):
        if CURRENT.get() is not None or self.stage not in STAGES: raise Blocked("INVALID_SCOPE")
        if self.allow_application_failure:
            if self.stage != 'agent':raise Blocked('INVALID_SCOPE')
            self.before = self.ledger.report()['used']
        self.token = CURRENT.set(self)
        return self

    def verify(self, condition):
        if condition is not True:
            self.ledger.halt()
            raise Blocked("FUNCTIONAL_VALIDATION_FAILED")
        self.verified = True

    def __exit__(self, typ, value, tb):
        CURRENT.reset(self.token)
        if typ is not None:
            if self.allow_application_failure:
                from app.core.runtime_safety import stop_settled_application_failure
                if stop_settled_application_failure(self, value, self.before):return False
            self.ledger.halt()
        else:
            self.ledger.advance(self.stage, self.verified)

def forbid_extra(reason):
    scope = CURRENT.get()
    if scope:
        if getattr(scope,'authorization_expired',False) and reason in ('CHAT_FAILED','AGENT_FAILED'):
            raise Blocked('SESSION_OR_PRICE_EXPIRED')
        scope.ledger.halt(reason)
        raise Blocked(reason)

def require_tool(name):
    scope = CURRENT.get()
    if scope:
        with scope.ledger.transaction() as (c,r):
            dev=scope.ledger.development(c)
            if dev:
                if scope.stage!='agent' or name not in json.loads(dev['tools']):scope.ledger.deny(c,'UNAUTHORIZED_TOOL')
                return
    if scope and (scope.stage != "agent" or name != "calculator"):
        forbid_extra("CALCULATOR_ONLY")

def tools_for_scope(tools):
    scope=CURRENT.get()
    if scope:
        with scope.ledger.transaction() as (c,r):
            dev=scope.ledger.development(c)
            if dev:return [t for t in tools if t['function']['name'] in json.loads(dev['tools'])]
    return [t for t in tools if t["function"]["name"] == "calculator"] if CURRENT.get() else tools

def bounded_agent_limits(settings):
    if CURRENT.get():return min(settings.agent_max_rounds,6),min(settings.agent_max_tool_calls,8)
    return settings.agent_max_rounds,settings.agent_max_tool_calls

def controlled_payload(payload):
    if CURRENT.get():
        payload = dict(payload, max_tokens=min(payload["max_tokens"],getattr(CURRENT.get(),'output_limit',512)), enable_thinking=False)
        if payload.get("stream"): payload["stream_options"] = {"include_usage":True}
    return payload

def _preflight(settings, kind, request, provided):
    scope = CURRENT.get()
    controlled = getattr(settings, "live_test_mode", False)
    mocked = isinstance(provided, httpx.MockTransport)
    try: local = ipaddress.ip_address(request.url.host).is_loopback
    except ValueError: local = request.url.host == "localhost"
    if not scope:
        if controlled or (not mocked and not (local and request.url.host and str(getattr(settings, "llm_model" if kind == "llm" else "embedding_model", "")).startswith(("TEST-ONLY", "unit-test-")))): raise Blocked("EXPLICIT_RUN_AUTHORIZATION_REQUIRED")
        return None
    if not controlled or Path(settings.live_test_ledger).resolve() != scope.ledger.path:
        forbid_extra("LEDGER_CONFIGURATION_MISMATCH")
    if kind not in MODELS: forbid_extra("RERANKER_FORBIDDEN")
    if scope.offline and not mocked: forbid_extra("OFFLINE_TRANSPORT_REQUIRED")
    if not scope.offline and provided is not None: forbid_extra("LIVE_TRANSPORT_MUST_HAVE_ZERO_RETRIES")
    base = getattr(settings, "llm_base_url" if kind == "llm" else "embedding_base_url").rstrip("/")
    suffix = "/chat/completions" if kind == "llm" else "/embeddings"
    if request.url.username or request.url.password or request.url.query or request.url.fragment:
        forbid_extra("INVALID_HTTP_DESTINATION")
    if request.method != "POST" or str(request.url) != base+suffix:
        forbid_extra("UNEXPECTED_HTTP_DESTINATION")
    if not scope.offline and request.url.scheme != "https": forbid_extra("HTTPS_REQUIRED")
    try: payload = json.loads(request.content)
    except Exception: forbid_extra("INVALID_JSON_PAYLOAD")
    from app.core.personal_learning import is_personal,fingerprint
    with scope.ledger.transaction() as (c,r):
        if is_personal(c,scope.ledger.run_id):
            if not getattr(settings,'personal_learning',False):raise Blocked('PERSONAL_MODE_REQUIRES_EXPLICIT_OPT_IN')
            binding=c.execute('SELECT p.binding FROM personal_prices p JOIN personal_runs s ON s.price_id=p.id WHERE s.run_id=?',(scope.ledger.run_id,)).fetchone()[0]
            if binding!=fingerprint(settings):raise Blocked('PERSONAL_MODEL_RECONFIRMATION_REQUIRED')
    seq = scope.ledger.reserve(scope.stage, kind, payload, offline=scope.offline)
    ATTEMPT_TICKET.set((scope.ledger,seq))
    return scope.ledger, seq

def sanitize_error_text(value, redactions=()):
    if not isinstance(value,str):return ""
    for secret in sorted(set(redactions),key=len,reverse=True):
        value=value.replace(secret,"[REDACTED]")
    value=re.sub(r"(?i)bearer\s+[^\s,;\"<>]+", "Bearer [REDACTED]",value)
    value=re.sub(r"(?i)sk-[a-z0-9_-]+", "[REDACTED]",value)
    value=re.sub(r"(?i)(api[_ -]?key|authorization|secret|token)\s*[:=]\s*[^\s,;\"<>]+", r"\1=[REDACTED]",value)
    value=re.sub(r"https?://[^\s\"<>]+", "[URL REDACTED]",value)
    value=re.sub(r"[A-Za-z0-9_+/=-]{24,}", "[REDACTED]",value)
    value=' '.join(value.split())
    return value[:256]

def decode_captured_body(body, encoding, *, raw=True, limit=2_000_000):
    """Bounded decode of raw transport bytes. Already-read Mock responses are decoded by HTTPX."""
    if len(body)>limit:raise ValueError("DECODED_RESPONSE_TOO_LARGE")
    if not raw:return body
    codings=[x.strip().lower() for x in encoding.split(",") if x.strip()]
    if len(codings)>3:raise ValueError("UNSUPPORTED_CONTENT_ENCODING")
    for coding in reversed(codings):
        if coding=="identity":continue
        if coding not in ("gzip","x-gzip","deflate"):
            raise ValueError("UNSUPPORTED_CONTENT_ENCODING")
        modes=(16+zlib.MAX_WBITS,) if coding in ("gzip","x-gzip") else (zlib.MAX_WBITS,-zlib.MAX_WBITS)
        decoded=None
        for mode in modes:
            try:
                decoder=zlib.decompressobj(mode)
                candidate=decoder.decompress(body,limit+1)
                if len(candidate)>limit or decoder.unconsumed_tail:
                    raise ValueError("DECODED_RESPONSE_TOO_LARGE")
                if not decoder.eof or decoder.unused_data:
                    raise ValueError("CONTENT_DECODING_FAILED")
                decoded=candidate
                break
            except zlib.error:continue
        if decoded is None:raise ValueError("CONTENT_DECODING_FAILED")
        body=decoded
    return body


def parse_http_error(body, status, headers, redactions=()):
    claimed_json="json" in (headers.get("content-type") or "").lower()
    fallback="Response declared JSON but JSON parsing failed; body omitted." if claimed_json else "Non-JSON response body omitted."
    result={"http_status":status,"code":"HTTP_"+str(status),"message":fallback,"request_id":None}
    try:
        data=json.loads(body)
        if isinstance(data,dict):
            detail=data.get("error",data)
            obj=detail if isinstance(detail,dict) else data
            result["code"]=sanitize_error_text(obj.get("code",obj.get("Code")),redactions) or result["code"]
            message=obj.get("message",obj.get("Message"))
            if message is None and isinstance(detail,str):message=detail
            if message is None and isinstance(data.get("detail"),str):message=data["detail"]
            result["message"]=sanitize_error_text(message,redactions) or "JSON error response has no supported message field."
            result["request_id"]=obj.get("request_id",obj.get("RequestId",obj.get("requestId"))) or data.get("request_id",data.get("RequestId",data.get("requestId")))
        else:result["message"]="JSON error response has unsupported structure; body omitted."
    except (ValueError,TypeError):pass
    for name in ("x-request-id","x-dashscope-request-id","x-acs-request-id","request-id"):
        result["request_id"]=result["request_id"] or headers.get(name)
    request_id=result["request_id"]
    result["request_id"]=(request_id[:128] if isinstance(request_id,str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",request_id) and not request_id.lower().startswith("sk-") and not any(secret in request_id for secret in redactions) else None)
    return result

class Capture:
    def __init__(self, ticket, response, redactions=()):
        self.redactions=redactions
        self.ticket, self.status = ticket, response.status_code
        self.streaming = "text/event-stream" in response.headers.get("content-type", "")
        self.headers = {name:response.headers.get(name) for name in ("content-type","x-request-id","x-dashscope-request-id","x-acs-request-id","request-id")}
        self.content_encoding=response.headers.get("content-encoding", "")
        self.raw_body=not response.is_stream_consumed
        self.buffer = bytearray()
        self.done = False

    def feed(self, chunk):
        if len(self.buffer)+len(chunk)>2_000_000: raise Blocked("RESPONSE_TOO_LARGE")
        self.buffer.extend(chunk)

    def finish(self, error=None):
        if self.done: return
        self.done = True
        usage = None
        finish_reason=None
        decode_error=None
        try:body=decode_captured_body(bytes(self.buffer),self.content_encoding,raw=self.raw_body)
        except ValueError as exc:
            body=b""
            decode_error=str(exc)  # Only one of the fixed decoder reason codes above.
        if not 200 <= self.status < 300:
            details=parse_http_error(body,self.status,self.headers,self.redactions)
            if decode_error:details["message"]="Response content decoding failed ("+decode_error+"); body omitted."
            ledger,seq=self.ticket
            ledger.settle(seq,self.status,error="HTTP_"+str(self.status),error_details=details)
            return
        if decode_error:error=decode_error
        if error == "INTERRUPTED" and self.streaming and b"data: [DONE]" in body:
            error = None
        try:
            if not error:
                if self.streaming:
                    for line in body.decode("utf-8").splitlines():
                        if line.startswith("data:") and line[5:].strip() != "[DONE]":
                            event = json.loads(line[5:])
                            if event.get("usage") is not None: usage = event["usage"]
                            for choice in event.get('choices',[]):
                                if choice.get('finish_reason') is not None:finish_reason=choice['finish_reason']
                else:
                    parsed=json.loads(body);usage=parsed.get("usage")
                    for choice in parsed.get('choices',[]):
                        if choice.get('finish_reason') is not None:finish_reason=choice['finish_reason']
        except Exception: error = "USAGE_PARSE_ERROR"
        ledger, seq = self.ticket
        reason={'stop':'STOP','length':'LENGTH','tool_calls':'TOOL_CALLS','content_filter':'CONTENT_FILTER'}.get(finish_reason,'OTHER') if finish_reason is not None else None
        ledger.settle(seq, self.status, usage, error,finish_reason=reason)

class SyncCapture(httpx.SyncByteStream):
    def __init__(self, stream, capture): self.stream, self.capture = stream, capture
    def __iter__(self):
        try:
            for chunk in self.stream:
                self.capture.feed(chunk)
                yield chunk
            self.capture.finish()
        except BaseException as exc:
            self.capture.finish(type(exc).__name__)
            raise
    def close(self):
        try: self.stream.close()
        finally: self.capture.finish("INTERRUPTED")

class AsyncCapture(httpx.AsyncByteStream):
    def __init__(self, stream, capture): self.stream, self.capture = stream, capture
    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.capture.feed(chunk)
                yield chunk
            self.capture.finish()
        except BaseException as exc:
            self.capture.finish(type(exc).__name__)
            raise
    async def aclose(self):
        try: await self.stream.aclose()
        finally: self.capture.finish("INTERRUPTED")

class GuardSync(httpx.BaseTransport):
    def __init__(self, settings, kind, provided):
        self.settings,self.kind,self.provided = settings,kind,provided
        self.inner = provided if provided is not None else model_transport(settings,kind)
    def handle_request(self, request):
        from app.core.development_budget import prepare_retry
        token=ATTEMPT_TICKET.set(None)
        try:
            for attempt in range(2):
                ATTEMPT_TICKET.set(None)
                try:return self._once(request)
                except Blocked:
                    ticket=ATTEMPT_TICKET.get()
                    if attempt or not ticket or not prepare_retry(*ticket):raise
                    time.sleep(0.25)
        finally:ATTEMPT_TICKET.reset(token)
    def _once(self, request):
        ticket = _preflight(self.settings,self.kind,request,self.provided)
        observer=PROXY_OBSERVER.get()
        try:
            if observer:observer.attach(request,self.settings,self.kind)
            response = self.inner.handle_request(request)
            if observer:observer.response(response)
        except BaseException as exc:
            if observer:observer.failure(exc)
            if ticket: ticket[0].settle(ticket[1],None,error=type(exc).__name__)
            raise
        if ticket:
            with ticket[0].transaction() as (c,r):dev=ticket[0].development(c)
            if dev and not response.is_success:
                try:response.read()
                except Exception as exc:ticket[0].settle(ticket[1],None,error=type(exc).__name__)
            capture = Capture(ticket,response,credential_redactions(self.settings,self.kind) if self.provided is None else ())
            if response.is_stream_consumed:
                capture.feed(response.content)
                capture.finish()
            else:
                response.stream = SyncCapture(response.stream,capture)
        return response
    def close(self): self.inner.close()

class GuardAsync(httpx.AsyncBaseTransport):
    def __init__(self, settings, kind, provided):
        self.settings,self.kind,self.provided = settings,kind,provided
        self.inner = provided if provided is not None else model_transport(settings,kind,asynchronous=True)
    async def handle_async_request(self, request):
        import asyncio
        from app.core.development_budget import prepare_retry
        token=ATTEMPT_TICKET.set(None)
        try:
            for attempt in range(2):
                ATTEMPT_TICKET.set(None)
                try:return await self._once(request)
                except Blocked:
                    ticket=ATTEMPT_TICKET.get()
                    if attempt or not ticket or not prepare_retry(*ticket):raise
                    await asyncio.sleep(0.25)
        finally:ATTEMPT_TICKET.reset(token)
    async def _once(self, request):
        ticket = _preflight(self.settings,self.kind,request,self.provided)
        try:
            response = await self.inner.handle_async_request(request)
        except BaseException as exc:
            if ticket: ticket[0].settle(ticket[1],None,error=type(exc).__name__)
            raise
        if ticket:
            with ticket[0].transaction() as (c,r):dev=ticket[0].development(c)
            if dev and not response.is_success:
                try:await response.aread()
                except Exception as exc:ticket[0].settle(ticket[1],None,error=type(exc).__name__)
            capture = Capture(ticket,response,credential_redactions(self.settings,self.kind) if self.provided is None else ())
            if response.is_stream_consumed:
                capture.feed(response.content)
                capture.finish()
            else:
                response.stream = AsyncCapture(response.stream,capture)
        return response
    async def aclose(self): await self.inner.aclose()
