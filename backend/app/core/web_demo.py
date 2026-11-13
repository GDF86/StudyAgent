"""Opt-in loopback-only ASGI boundary; scope survives threadpool and SSE send."""
import asyncio,json,time,re,logging
from collections import deque
from app.core.live_test import Ledger,Stage,CURRENT,Blocked
from starlette.responses import JSONResponse

class WebDemoMiddleware:
    def __init__(self,app,settings):
        self.app,self.settings=app,settings
        self.ledger=Ledger(settings.live_test_ledger,settings.web_demo_round_id)
        if not settings.live_test_mode or not settings.web_demo_round_id:raise RuntimeError('DEMO_REQUIRES_EXPLICIT_LEDGER')
        with self.ledger.transaction() as (c,r):
            if not self.ledger.development(c):raise RuntimeError('DEMO_SESSION_NOT_READY')
            from app.core.personal_learning import is_personal
            if is_personal(c,self.ledger.run_id) and not getattr(settings,'personal_learning',False):raise RuntimeError('PERSONAL_MODE_REQUIRES_EXPLICIT_OPT_IN')
        self.busy=False;self.recent=deque()

    def session_status(self):
        try:
            with self.ledger.transaction() as (c,r):
                policy=self.ledger.development(c);now=time.time()
                expired=not policy or now>=policy['expires']
                price_expired=not r['verified_until'] or now>=r['verified_until']
                used=c.execute('SELECT count(*) FROM requests WHERE run_id=?',(self.ledger.run_id,)).fetchone()[0]
                pending=bool(c.execute("SELECT 1 FROM requests WHERE status='PENDING'").fetchone())
                exhausted=bool(policy and used>=policy['max_requests'])
                valid=r['state']=='READY' and bool(r['authorized']) and not expired and not price_expired and not exhausted
                from app.core.development_budget import risk
                occupied=risk(c)
                from app.core.personal_learning import is_personal,saved
                personal=is_personal(c,self.ledger.run_id)
                profile=saved(c) if getattr(self.settings,'personal_learning',False) else None
                if personal:valid=r['state']=='READY' and bool(r['authorized']) and not exhausted
                binding_changed=False
                if profile:
                    from app.core.personal_learning import fingerprint
                    try:binding_changed=profile['binding']!=fingerprint(self.settings)
                    except Blocked:binding_changed=True
                    if binding_changed:valid=False
                money_block=not personal and occupied>=50_000_000_000
                reason='SAFETY_HALTED' if r['state']=='HALTED' else 'PRICE_CONFIGURATION_CHANGED' if binding_changed else 'PROJECT_MONEY_LIMIT' if money_block else 'PENDING_REQUEST' if pending else 'READY' if valid else 'SESSION_EXPIRED' if expired else 'PRICE_CONFIRMATION_EXPIRED' if price_expired else 'REAUTHORIZATION_REQUIRED'
                renewable=bool(getattr(self.settings,'web_demo_allow_session_management',False) and r['state'] in ('READY','COMPLETE') and not valid and not pending and (getattr(self.settings,'personal_learning',False) or occupied<50_000_000_000))
                halt_reason=None
                if r['state']=='HALTED':
                    halt_reason='LEGACY_REASON_NOT_RECORDED'
                    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_events'").fetchone():
                        row=c.execute("SELECT reason FROM runtime_events WHERE run_id=? AND kind='SAFETY_HALT' ORDER BY id DESC LIMIT 1",(self.ledger.run_id,)).fetchone()
                        if row:
                            from app.core.runtime_safety import code
                            halt_reason=code(row[0])
                return {'session_valid':bool(valid and not pending and (getattr(self.settings,'personal_learning',False) or occupied<50_000_000_000)),'session_expired':bool(expired),'price_expired':bool(price_expired),'session_ready':r['state']=='READY',
                        'model_state':reason,'session_version':self.ledger.run_id,'can_reauthorize':renewable,'management_enabled':bool(getattr(self.settings,'web_demo_allow_session_management',False)),
                        'personal_mode':bool(getattr(self.settings,'personal_learning',False)),'price_confirmed_at':profile['confirmed'] if profile else None,'fee_reminders':[x for x in json.loads(profile['thresholds']) if occupied>=x*1e9] if profile else [],'price_source':'最近一次人工确认价格' if profile else None,'policy_version':profile['version'] if profile else 'development-v1','session_known_cost_yuan':c.execute('SELECT coalesce(sum(cost_nano),0) FROM requests WHERE run_id=?',(self.ledger.run_id,)).fetchone()[0]/1e9,
                        'halt_reason':halt_reason,'llm_configured':bool(getattr(self.settings,'llm_model',None) and getattr(self.settings,'llm_base_url',None) and getattr(self.settings,'llm_api_key',None)),'embedding_configured':bool(getattr(self.settings,'embedding_model',None) and getattr(self.settings,'embedding_base_url',None) and getattr(self.settings,'embedding_api_key',None)),
                        'known_cost_yuan':c.execute('SELECT coalesce(sum(cost_nano),0) FROM requests').fetchone()[0]/1e9,
                        'project_risk_yuan':occupied/1e9,'budget_alert':occupied>=30_000_000_000,'session_expires_at':policy['expires'] if policy else None,'price_expires_at':r['verified_until']}
        except Blocked:return {'session_valid':False,'session_expired':None,'price_expired':None,'session_ready':False,'model_state':'LEDGER_UNAVAILABLE','can_reauthorize':False}

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]));host=headers.get(b'host',b'').decode()
        path=scope['path'];method=scope['method']
        host_match=host.split(':')[0]=='127.0.0.1'
        origin_match=headers.get(b'origin',b'').decode() in self.settings.cors_origins
        document_route=bool(re.fullmatch(r'/api/(?:knowledge-bases(?:/\d+(?:/documents(?:/preview)?)?)?|documents/[0-9a-fA-F-]+(?:/(?:processing-plan|retry))?)',path))
        safe_path=re.sub(r'/[0-9a-fA-F-]{20,}(?=/|$)','/:document_id',path) if document_route else ('/api/chat' if path.startswith('/api/chat') else '/api/agent' if path.startswith('/api/agent') else 'UNSUPPORTED_ROUTE')
        def diagnostic(status,reason):
            return {'method':method,'route':safe_path,'status':status,'reason':reason,'writes_enabled':bool(getattr(self.settings,"web_demo_allow_writes",False)),
                        'session_valid':self.session_status()['session_valid'],'origin_match':origin_match,'host_match':host_match,'allowed_local_document_route':document_route}
        async def reject(status,message,reason='LOCAL_REQUEST_REJECTED'):
            fields=diagnostic(status,reason)
            logging.getLogger('studyagent.permissions').warning(json.dumps(fields,ensure_ascii=True))
            await JSONResponse({'code':'WEB_DEMO_BLOCKED','message':message,'detail':fields},status_code=status)(scope,receive,send)
        if (scope.get('client') or ('',))[0]!='127.0.0.1' or not host_match:return await reject(403,'仅允许127.0.0.1直接访问','NON_LOOPBACK_OR_HOST')
        if path=='/api/local-mode-status' and method=='GET':
            return await JSONResponse({'document_api_version':2,'document_writes':bool(getattr(self.settings,"web_demo_allow_writes",False)),**self.session_status()})(scope,receive,send)
        if path=='/api/local-model-session/reauthorize' and method=='POST':
            if not origin_match or not getattr(self.settings,'web_demo_allow_session_management',False):return await reject(403,'本机模型管理未开放或来源不匹配','MANAGEMENT_NOT_ALLOWED')
            if self.busy:return await reject(409,'已有本机任务执行中，不能重新授权','REQUEST_IN_PROGRESS')
            body=bytearray()
            while True:
                event=await receive()
                if event['type']!='http.request':return
                body.extend(event.get('body',b''))
                if len(body)>4096:return await reject(413,'授权表单过长')
                if not event.get('more_body'):break
            try:
                from decimal import Decimal,InvalidOperation
                data=json.loads(body)
                if not isinstance(data,dict) or data.get('confirmed') is not True or data.get('region')!='beijing' or data.get('input_tier')!='<=128K':raise ValueError()
                rates=[]
                for key in ('llm_input_per_million','llm_output_per_million','embedding_input_per_million'):
                    raw=str(data.get(key))
                    if len(raw)>24 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,3})?',raw):raise ValueError()
                    value=Decimal(raw)*1000
                    if not value.is_finite() or value!=value.to_integral_value() or not 0<value<=10_000_000:raise ValueError()
                    rates.append(int(value))
            except (ValueError,TypeError,InvalidOperation):return await reject(400,'请核对北京地域官方价格、输入档位并明确确认；不能沿用过期价格','CURRENT_PRICE_CONFIRMATION_REQUIRED')
            if self.settings.llm_model!='qwen-flash' or self.settings.embedding_model!='text-embedding-v4' or self.settings.enable_reranker:return await reject(403,'模型配置超出本机授权范围','MODEL_CONFIGURATION_CHANGED')
            try:
                from app.core.development_budget import renew_web_session
                if getattr(self.settings,'personal_learning',False):
                    from app.core.personal_learning import ensure
                    renewed=ensure(self.ledger,self.settings,{'expected':data.get('session_version'),'confirmed':True,'input_rate':rates[0],'output_rate':rates[1],'embedding_rate':rates[2]})
                else:renewed=renew_web_session(self.ledger,data.get('session_version'),input_rate=rates[0],output_rate=rates[1],embedding_rate=rates[2],confirmed=True)
            except Blocked as error:
                return await JSONResponse({'code':'MODEL_REAUTHORIZATION_BLOCKED','message':error.message},status_code=409)(scope,receive,send)
            self.ledger=renewed;self.settings.web_demo_round_id=renewed.run_id
            return await JSONResponse(self.session_status())(scope,receive,send)
        if method=='GET' and path.endswith('/documents/preview'):
            return await reject(405,'新上传预览需使用POST multipart；已有失败文件请使用GET /api/documents/{id}/processing-plan','PREVIEW_METHOD_MISMATCH')
        plan=method=='GET' and bool(re.fullmatch(r'/api/documents/[0-9a-fA-F-]+/processing-plan',path))
        if method in ('GET','HEAD','OPTIONS') and not plan:return await self.app(scope,receive,send)
        model=method=='POST' and path in ('/api/chat','/api/chat/stream','/api/agent/run','/api/agent/stream')
        upload=method=='POST' and bool(re.fullmatch(r'/api/knowledge-bases/\d+/documents(?:/preview)?',path))
        retry=method=='POST' and bool(re.fullmatch(r'/api/documents/[0-9a-fA-F-]+/retry',path))
        history=(method=='POST' and path=='/api/conversations') or (method in ('PUT','DELETE') and bool(re.fullmatch(r'/api/conversations/[0-9a-fA-F-]+',path)))
        cancel=method=='POST' and bool(re.fullmatch(r'/api/conversations/[0-9a-fA-F-]+/requests/[0-9a-fA-F-]+/cancel',path))
        preference=method in ('PUT','DELETE') and bool(re.fullmatch(r'/api/memory/(?:answer_style|learning_goal|preferred_language)',path))
        comparison=method=='POST' and path=='/api/rag/compare'
        requires_model=model or comparison or retry or (upload and not path.endswith('/preview'))
        metadata=(method=='POST' and path=='/api/knowledge-bases') or (method in ('PUT','DELETE') and bool(re.fullmatch(r'/api/knowledge-bases/\d+',path))) or (method=='DELETE' and bool(re.fullmatch(r'/api/documents/[0-9a-fA-F-]+',path)))
        if not model:
            if not (upload or metadata or retry or plan or history or cancel or preference or comparison):return await reject(403,'该接口或请求方法未开放','ROUTE_NOT_ALLOWED')
            if not getattr(self.settings,'web_demo_allow_writes',False):return await reject(403,'运行中的后端未启用本机资料写权限，请核对当前进程配置','WRITES_DISABLED')
        if not origin_match:return await reject(403,'请求Origin不在本机允许列表，请使用127.0.0.1:5189','ORIGIN_MISMATCH')
        if requires_model and getattr(self.settings,'personal_learning',False) and not self.busy:
            try:
                from app.core.personal_learning import ensure
                self.ledger=ensure(self.ledger,self.settings);self.settings.web_demo_round_id=self.ledger.run_id
            except Blocked as exc:
                await JSONResponse({'code':'MODEL_AUTH_REQUIRED','message':exc.message},status_code=403)(scope,receive,send);return
        state=self.session_status() if requires_model else None
        if state and not state['session_valid']:
            reason='SESSION_EXPIRED' if state['session_expired'] else 'PRICE_CONFIRMATION_EXPIRED' if state['price_expired'] else 'SESSION_NOT_READY'
            await JSONResponse({'code':'MODEL_AUTH_REQUIRED','message':'本机服务正常，但模型授权已过期、停止或有未完成请求。可继续浏览和保存本地设置，请查看顶部模型状态。','detail':{'reason':state.get('model_state',reason)}},status_code=403)(scope,receive,send)
            return
        if plan:return await self.app(scope,receive,send)
        if cancel:return await self.app(scope,receive,send)
        if method=='DELETE' and headers.get(b'x-confirm-delete')!=b'true':return await reject(403,'删除不可恢复，请通过页面确认弹窗执行')
        if self.busy:return await reject(409,'已有Demo任务正在执行，请等待完成')
        now=time.monotonic()
        while self.recent and self.recent[0]<now-60:self.recent.popleft()
        if len(self.recent)>=12:return await reject(429,'Demo操作过于频繁，请稍后再试')
        body=bytearray()
        while True:
            event=await receive()
            if event['type']!='http.request':return
            body.extend(event.get('body',b''))
            limit=self.settings.max_upload_bytes+65536 if upload else 64000
            if len(body)>limit:return await reject(413,'文件超过本机上传限制' if upload else 'Demo输入过长')
            if not event.get('more_body'):break
        data={}
        if not upload and method!='DELETE':
            try:data=json.loads(body)
            except ValueError:return await reject(400,'请求JSON无效')
            if not isinstance(data,dict):return await reject(400,'请求JSON必须是对象')
        if self.busy:return await reject(409,'已有本机任务正在执行，请等待完成')
        sent=False;status=500;chunks=bytearray();complete=False;disconnected=False
        async def replay():
            nonlocal sent,disconnected
            if not sent:sent=True;return {'type':'http.request','body':bytes(body),'more_body':False}
            event=await receive()
            if event['type']=='http.disconnect':disconnected=True
            return event
        if not requires_model:
            self.busy=True;self.recent.append(now)
            try:return await self.app(scope,replay,send)
            finally:self.busy=False
        before=self.ledger.report()['used']
        self.busy=True;self.recent.append(now)
        async def audit(event):
            nonlocal status,complete
            if event['type']=='http.response.start':status=event['status']
            if event['type']=='http.response.body':
                chunks.extend(event.get('body',b''))
                if len(chunks)>2_000_000:raise Blocked('DEMO_RESPONSE_TOO_LARGE')
                if not event.get('more_body',False):complete=True
            await send(event)
        stage='ingestion' if upload or retry else ('agent' if '/agent/' in path else ('stream' if path.endswith('/stream') else 'rag'))
        model_scope=Stage(self.ledger,stage,offline=False)
        model_scope.output_limit=1024
        model_scope.learning_output_policy=True
        model_scope.document_writes=bool(upload or retry)
        model_scope.history_enabled=bool(model and data.get('conversation_id'))
        token=CURRENT.set(model_scope)
        try:
            await self.app(scope,replay,audit)
            if getattr(model_scope,'application_error',None):
                from app.core.runtime_safety import settled_request_boundary
                if not settled_request_boundary(self.ledger,before):raise Blocked('APPLICATION_ERROR_WITH_UNSETTLED_CHARGE')
                self.ledger.record_event('REQUEST_STOPPED',model_scope.application_error);return
            if getattr(model_scope,'output_limited',False):
                from app.core.runtime_safety import settled_request_boundary
                if not settled_request_boundary(self.ledger,before):raise Blocked('OUTPUT_LIMIT_WITH_UNSETTLED_CHARGE')
                self.ledger.record_event('REQUEST_STOPPED','LLM_OUTPUT_LIMIT')
                return
            if status>=400:
                try:code=json.loads(chunks).get('code','API_REJECTED')
                except (ValueError,AttributeError):code='API_REJECTED'
                if code not in ('RETRY_CONFIRMATION_REQUIRED','REFERENCE_KB_PROTECTED','DOCUMENT_NOT_FAILED','VALIDATION_ERROR','FILE_TOO_LARGE','UPLOAD_MODEL_LIMIT','TOO_MANY_CHUNKS','LIVE_TEST_BLOCKED'):code='API_REJECTED'
                logging.getLogger('studyagent.permissions').warning(json.dumps(diagnostic(status,code)))
                if self.ledger.report()['used']>before and not getattr(model_scope,'authorization_expired',False):self.ledger.halt('WEB_APPLICATION_ERROR')
                return
            if not complete:raise Blocked('DEMO_HTTP_OR_STREAM_FAILED')
            if stage=='stream' or scope['path'].endswith('/stream'):
                frames=[json.loads(x[5:]) for x in chunks.decode().splitlines() if x.startswith('data:')]
                if complete and any(x.get('type')=='error' for x in frames):
                    self.ledger.halt('WEB_STREAM_INCOMPLETE');return
                if not any(x.get('type')=='done' for x in frames) or any(x.get('type')=='error' for x in frames):raise Blocked('DEMO_SSE_INCOMPLETE')
            with self.ledger.transaction() as (c,r):
                if r['state']!='READY':raise Blocked('DEMO_SESSION_HALTED')
        except BaseException as exc:
            from app.core.runtime_safety import settled_request_boundary
            if (disconnected or isinstance(exc,asyncio.CancelledError)) and settled_request_boundary(self.ledger,before):
                self.ledger.record_event('REQUEST_STOPPED','CLIENT_CANCELLED_WITH_KNOWN_USAGE')
                if isinstance(exc,asyncio.CancelledError):raise
                return
            limited=getattr(model_scope,'output_limited',False) and settled_request_boundary(self.ledger,before)
            if limited:self.ledger.record_event('REQUEST_STOPPED','LLM_OUTPUT_LIMIT')
            expired=getattr(model_scope,'authorization_expired',False)
            if not expired and not limited:self.ledger.halt('WEB_STREAM_INCOMPLETE' if isinstance(exc,Blocked) else 'WEB_APPLICATION_EXCEPTION')
            if status==200 and not complete:
                error={'type':'error','code':'LLM_OUTPUT_LIMIT' if limited else 'MODEL_AUTH_REQUIRED' if expired else 'LIVE_TEST_BLOCKED',
                       'message':'回答达到输出上限，未完整完成；本次调用仍计费，请缩小问题后主动提问。' if limited else '模型授权在任务执行期间到期，后续请求未发送。已完成调用仍计费，请核价后重新授权，不会自动重试。' if expired else 'Demo task failed; session halted. Check model output limit or ledger.'}
                await send({'type':'http.response.body','body':('data: '+json.dumps(error,ensure_ascii=False,separators=(',',':'))+'\n\n').encode(),'more_body':False})
                return
            if (expired or limited) and complete:return
            raise
        finally:
            CURRENT.reset(token);self.busy=False
