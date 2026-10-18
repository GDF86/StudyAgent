"""TEST ONLY scripted tool-call fixtures, not a language/embedding model.

Extends the unchanged Phase 3 protocol fixture for full regression.
Run with python tests/agent_stub_server.py; bound to loopback port 18084.
"""
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
import re
import time
from rag_stub_server import Handler as RagHandler


def tool(name, arguments, id):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def response(answer=None, calls=None):
    return {"choices": [{"message": {"role": "assistant", "content": answer if calls is None else "PRIVATE_TEST_REASONING",
            **({"tool_calls": calls} if calls is not None else {})}, "finish_reason": "stop" if calls is None else "tool_calls"}]}


class Handler(RagHandler):
    def respond(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        body = json.loads(raw)
        if self.path == '/v1/chat/completions' and 'tools' in body:
            time.sleep(0.08)
            initial = json.loads(body['messages'][1]['content'])
            task, docs = initial['task'], initial['documents']
            returned = [json.loads(m['content']) for m in body['messages'] if m['role'] == 'tool']
            count = len(returned)
            if task == 'MOCK:timeout': time.sleep(3)
            if task == 'MOCK:error':
                self.json_response(500, {'error': 'TEST ONLY failure'})
                return
            if task == 'MOCK:limit':
                calls = [tool('calculator', {'expression': '1+1'}, f'loop-{count}')]
            elif task == 'MOCK:recover':
                calls = [tool('calculator', {'expression': '1/0' if count == 0 else '7*6'}, f'recover-{count}')] if count < 2 else None
            elif task == 'MOCK:all':
                if count == 0:
                    calls = [tool('search_documents', {'query': 'ALPHA', 'top_k': 2}, 'search'), tool('calculator', {'expression': '6*7'}, 'calc')]
                elif count == 2:
                    calls = [tool('summarize_document', {'document_id': docs[0]['document_id']}, 'summary'),
                             tool('compare_documents', {'document_ids': [d['document_id'] for d in docs[:2]]}, 'compare'),
                             tool('generate_quiz', {'topic': 'ALPHA', 'count': 1, 'question_type': 'short_answer'}, 'quiz')]
                else: calls = None
            else:
                # Explicit fixture default, not production intent classification.
                calls = [tool('calculator', {'expression': '6*7'}, 'calc')] if count == 0 else None
            answer = 'Mock 协议测试完成，实际计算结果 42。'
            if calls is None and task == 'MOCK:all':
                if any(not r['ok'] for r in returned):
                    self.json_response(500, {'error': 'A real tool failed in the fixture flow'})
                    return
                refs = sorted({c['id'] for r in returned for c in r.get('result', {}).get('citations', [])})
                answer += ' ' + ' '.join(f'[{n}]' for n in refs)
                answer += '\n\n' + returned[2]['result']['text'] + '\n\n' + returned[3]['result']['text']
                q = returned[4]['result']['questions'][0]
                answer += f"\n\n练习题：{q['question']}\n\n答案：{q['answer']}\n\n解释：{q['explanation']}"
            self.json_response(200, response(answer, calls))
            return
        if self.path == '/v1/chat/completions':
            context = json.loads(body['messages'][-1]['content'])
            if context.get('purpose') == 'conversation_summary':
                self.json_response(200, response('Mock 历史摘要：已讨论 ALPHA 学习资料，继续回答学习问题。'))
                return
            question = context['question']
            if question.startswith(('总结这篇文档', '比较这些文档', '生成 ')):
                refs = context['reference_material']
                answer = 'Mock 固定总结/比较（非真实模型能力）：' + ' '.join(f"{r['content']} [{r['id']}]" for r in refs)
                if question.startswith('生成 '):
                    count = int(re.search(r'生成 (\d+) 道', question).group(1))
                    answer = json.dumps({'questions': [{'question': 'ALPHA 资料的测试内容是什么？', 'options': [],
                        'answer': '本地测试原文', 'explanation': 'Mock 固定解释，仅验证结构和来源链路。', 'citation_ids': [refs[0]['id']]} for _ in range(count)]}, ensure_ascii=False)
                self.json_response(200, response(answer))
                return
        self.rfile = BytesIO(raw)
        super().respond()


if __name__ == '__main__':
    print('TEST ONLY scripted Agent/RAG service at http://127.0.0.1:18084/v1; no real model.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 18084), Handler).serve_forever()
