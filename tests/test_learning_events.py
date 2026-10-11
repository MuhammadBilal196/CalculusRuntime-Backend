import json
import tempfile
import unittest
from unittest.mock import patch
from starlette.requests import Request
from core import db, storage
from routers.progress import learning_events, validate_learning_event


def request(method='POST', body=None, after='0'):
    raw = json.dumps(body).encode()
    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    return Request({'type': 'http', 'method': method, 'path': '/learning-events',
                    'headers': [], 'query_string': ('after='+after).encode()}, receive)


def event(event_id='review-1'):
    return {'eventId': event_id, 'kind': 'flashcard', 'data': {
        'cardId': 'la-vectors:Dot product', 'rating': 'Good', 'occurredAt': '2026-10-06T10:00:00.000Z'}}


class LearningEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = patch.object(db, 'DB_PATH', self.tmp.name+'/test.db'); self.path.start()
        self.backend = patch.object(storage, 'USE_SUPABASE', False); self.backend.start()
        self.auth = patch('routers.progress.require_user', return_value=1); self.auth.start()
        self.addCleanup(patch.stopall)
        await db.init_db()
        await db.execute("INSERT INTO users(id,username,hashed_pw) VALUES (1,'one','x'),(2,'two','y')")

    async def test_idempotent_append_survives_repeated_requests(self):
        first = await learning_events(request(body=event()))
        second = await learning_events(request(body=event()))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.body, second.body)
        self.assertEqual(len(await storage.list_learning_events(1)), 1)

    async def test_users_have_separate_histories_and_cannot_choose_owner(self):
        await storage.append_learning_event(1, event())
        self.assertEqual(await storage.list_learning_events(2), [])
        await storage.append_learning_event(2, event())
        self.assertEqual(len(await storage.list_learning_events(2)), 1)
        body = event(); body['user_id'] = 2
        self.assertEqual((await learning_events(request(body=body))).status_code, 400)

    async def test_authentication_required(self):
        with patch('routers.progress.require_user', return_value=None):
            self.assertEqual((await learning_events(request(body=event()))).status_code, 401)
            self.assertEqual((await learning_events(request('GET'))).status_code, 401)

    async def test_reused_id_cannot_overwrite_earlier_event(self):
        await storage.append_learning_event(1, event())
        changed = event(); changed['data']['rating'] = 'Again'
        self.assertEqual((await learning_events(request(body=changed))).status_code, 400)
        self.assertEqual((await storage.list_learning_events(1))[0]['event']['data']['rating'], 'Good')

    async def test_cursor_is_account_scoped_and_paginates(self):
        for i in range(202): await storage.append_learning_event(1, event(str(i)))
        first = json.loads((await learning_events(request('GET'))).body)
        self.assertEqual(len(first['items']), 200)
        second = json.loads((await learning_events(request('GET', after=str(first['next_cursor'])))).body)
        self.assertEqual(len(second['items']), 2); self.assertIsNone(second['next_cursor'])
        self.assertEqual((await learning_events(request('GET', after='-1'))).status_code, 400)

    async def test_invalid_rating_timestamp_and_extra_credentials_rejected(self):
        for field,value in [('rating','Perfect'),('occurredAt','not-a-date'),('occurredAt','2026-10-06T10:00:00'),('accessToken','secret')]:
            body=event(); body['data'][field]=value
            self.assertEqual((await learning_events(request(body=body))).status_code, 400)
        body=event();body['data']['cardId']='x'*70000
        self.assertEqual((await learning_events(request(body=body))).status_code, 413)

    async def test_question_record_validation_and_no_certificate_side_effects(self):
        data = {'schemaVersion':1,'attemptId':'attempt1','responseId':'1','source':'guide',
                'courseId':'linear-algebra','quizId':'guide','questionId':'1','prompt':'Choose',
                'options':['wrong','right'],'selectedIndex':0,'correctIndex':1,'correct':False,
                'grading':'client','topic':'Vectors','difficulty':None,'occurredAt':'2026-10-06T10:00:00Z'}
        body={'eventId':'q1','kind':'question','data':data}
        self.assertEqual((await learning_events(request(body=body))).status_code, 201)
        self.assertEqual(await storage.list_quiz_scores(1), {})
        data['correct']=True
        self.assertEqual((await learning_events(request(body=body))).status_code, 400)
        data['correct']=False;data['selectedIndex']=True
        self.assertEqual((await learning_events(request(body=body))).status_code, 400)

    async def test_note_validation_and_delete_event(self):
        body = {'eventId':'n1','kind':'note','data':{'noteId':'n','sectionId':'s','courseId':'linear-algebra','title':'Vectors','path':'/linear-algebra/vectors/1#intro','text':'Remember basis','quote':'Independent','deleted':False,'occurredAt':'2026-10-07T10:00:00Z'}}
        self.assertEqual((await learning_events(request(body=body))).status_code, 201)
        body['eventId']='n2';body['data']['deleted']=True;body['data']['text']='';body['data']['quote']=''
        self.assertEqual((await learning_events(request(body=body))).status_code, 201)
        for path in ['//evil.example','https://evil.example','/\\evil.example']:
            body['data']['path']=path
            self.assertEqual((await learning_events(request(body=body))).status_code, 400)

    async def test_supabase_adapter_scopes_queries_and_uses_duplicate_ignore(self):
        from unittest.mock import MagicMock
        client=MagicMock(); builder=client.table.return_value
        for name in ['upsert','select','eq','gt','order','limit']:getattr(builder,name).return_value=builder
        body=event();payload=json.dumps(body,sort_keys=True,ensure_ascii=False,separators=(',',':'))
        builder.execute.return_value.error=None
        builder.execute.return_value.data=[{'id':5,'payload':payload}]
        with patch.object(storage,'USE_SUPABASE',True), patch.object(storage,'_supabase',client,create=True):
            self.assertEqual((await storage.append_learning_event(2,body))['id'],5)
            await storage.list_learning_events(2,3)
        builder.eq.assert_any_call('user_id',2)
        builder.gt.assert_called_with('id',3)
        self.assertTrue(builder.upsert.call_args.kwargs['ignore_duplicates'])
        self.assertEqual(builder.upsert.call_args.args[0]['user_id'],2)

if __name__ == '__main__': unittest.main()
