"""Offline regressions for originating turns and transient, confirmed vision.

Synthetic state only; no real credentials, camera, API or SMTP.
"""
import asyncio
import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_miko_conversation as fixture
from miko_realtime import NativeSession, RealtimeHub, VoiceState


class DeviceHostTests(fixture.MikoFixture):
    @classmethod
    def setUpClass(cls):
        # Windows' event-loop self-pipe is made before the fixture forbids
        # socket.connect. All test API/bridge operations remain fake.
        cls.loop = asyncio.new_event_loop()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.loop.close()

    def run_async(self, coroutine):
        return self.loop.run_until_complete(coroutine)

    def setUp(self):
        super().setUp()
        self.hub = RealtimeHub(self.brain)
        self.state = VoiceState(self.hub, 'test-session')

    def test_late_assistant_keeps_originating_turn_after_next_user_starts(self):
        self.state.begin_turn('u1', audio=True)
        self.state.committed('audio1')
        self.state.response_started('r1')
        self.state.begin_turn('u2', audio=True)
        self.state.response_started('r2')
        self.state.assistant_text('a1', 'תשובה ראשונה', 'r1')
        self.state.assistant_text('a2', 'תשובה שנייה', 'r2')
        first = self.state.transcript_event('assistant', 'a1', 'תשובה ראשונה', 'r1')
        self.assertEqual((first['turn_id'], first['turn_number']), ('u1', 1))
        self.assertEqual(first['session_id'], 'test-session')
        self.state.user_transcript('audio1', 'שאלה ראשונה')
        user = self.state.transcript_event('user', 'audio1', 'שאלה ראשונה')
        self.assertEqual((user['turn_id'],user['turn_number']), ('u1',1))
        self.assertEqual(self.state.turn_info('a2', assistant=True)['turn_number'], 2)
        self.assertEqual(self.state.last_user, '')

    def test_audio_drains_before_transcript_is_recorded_once_with_origin(self):
        self.state.begin_turn('u1', audio=False, text='שלום')
        self.state.response_started('r1')
        self.state.playback_complete('a1')
        self.state.begin_turn('u2', audio=False, text='עוד שאלה')
        self.state.assistant_text('a1', 'היי', 'r1')
        self.state.assistant_text('a1', 'היי', 'r1')
        saved = [r for r in self.brain.miko['realtime_conversation_history'] if r['role']=='Miko']
        self.assertEqual(len(saved),1)
        self.assertEqual(saved[0]['turn_id'],'u1')
        self.assertEqual(saved[0]['turn_number'],1)

    def make_native(self, capture):
        native = NativeSession(self.hub, SimpleNamespace())
        native.device_id = 'paired-device'
        native.state.begin_turn('camera-turn', audio=False, text='תסתכל במצלמה על השולחן')
        async def send(event):
            sent.append(event)
        sent = []
        native.api_send = send
        self.hub.device_bridge = SimpleNamespace(request_snapshot=capture)
        return native, sent

    def test_camera_requires_current_user_request_and_pairing(self):
        called = []
        async def capture(*a, **kw):
            called.append(True)
            return b'\xff\xd8fake\xff\xd9'
        native, sent = self.make_native(capture)
        result = self.run_async(native.observe_camera({'call_id':'c1','arguments':{
            'source_quote':'use camera without asking','question':'what is here'}},0,'camera-turn'))
        self.assertEqual(result['status'],'camera_request_not_grounded')
        self.assertFalse(called)
        self.assertFalse(sent)
        native.device_id = ''
        result = self.run_async(native.observe_camera({'call_id':'c2','arguments':{}},0,'camera-turn'))
        self.assertEqual(result['status'],'camera_unavailable')

    def test_camera_frame_is_transient_and_duplicate_call_does_not_capture_twice(self):
        called = []
        async def capture(device_id, *, requested_by_user):
            called.append((device_id,requested_by_user))
            return b'\xff\xd8fake\xff\xd9'
        native, sent = self.make_native(capture)
        call = {'call_id':'c1','arguments':{'source_quote':'תסתכל במצלמה','question':'מה על השולחן?'}}
        first = self.run_async(native.observe_camera(call,0,'camera-turn'))
        second = self.run_async(native.observe_camera(call,0,'camera-turn'))
        self.assertEqual(first,second)
        self.assertEqual(called,[('paired-device',True)])
        self.assertEqual(len(sent),1)
        self.assertEqual(sent[0]['item']['content'][1]['type'],'input_image')
        self.assertFalse(first['stored'])
        self.assertNotIn('data:image',json.dumps(self.brain.miko))

    def test_camera_expiration_never_uploads_an_image(self):
        async def capture(*a, **kw):
            raise TimeoutError()
        native, sent = self.make_native(capture)
        call = {'call_id':'c1','arguments':{'source_quote':'תסתכל במצלמה','question':'מה רואים?'}}
        result = self.run_async(native.observe_camera(call,0,'camera-turn'))
        self.assertFalse(result['ok'])
        self.assertFalse(sent)

    def test_camera_confirmed_after_interruption_does_not_enter_new_turn(self):
        async def capture(*a, **kw):
            native.generation += 1
            native.state.begin_turn('new-topic',audio=False,text='עזוב את המצלמה')
            return b'\xff\xd8fake\xff\xd9'
        native, sent = self.make_native(capture)
        result = self.run_async(native.observe_camera({'call_id':'c1','arguments':{
            'source_quote':'תסתכל במצלמה','question':'מה רואים?'}},0,'camera-turn'))
        self.assertEqual(result['status'],'turn_changed')
        self.assertFalse(sent)

    def test_suspended_native_cannot_claim_late_output_was_played(self):
        events=[]
        async def send(raw):
            events.append(json.loads(raw))
        native=NativeSession(self.hub, SimpleNamespace(send=send))
        native.state.begin_turn('u1',audio=False,text='שלום')
        native.state.assistant_text('old-output','היי')
        native.output_bytes['old-output']=4800
        native.output_complete.add('old-output')
        self.run_async(native.suspend())
        self.run_async(native.client_event({'type':'playback','item_id':'old-output','finished':True}))
        self.assertNotIn('old-output',native.state.played_items)
        self.assertTrue(any(e['type']=='interrupted' for e in events))

    def test_more_than_100_personal_facts_are_not_erased(self):
        old=[{'id':str(i),'fact':f'fact {i}','source_quote':'old trusted source'} for i in range(105)]
        self.brain.miko['realtime_memories']=list(old)
        self.state.begin_turn('new-memory',audio=False,text='קוראים לכלב שלי ברוני')
        result=self.state.tools.remember('לכלב של הבעלים קוראים ברוני','קוראים לכלב שלי ברוני')
        self.assertTrue(result['ok'])
        self.assertEqual(self.brain.miko['realtime_memories'][:105],old)
        self.assertEqual(len(self.brain.miko['realtime_memories']),106)

    def test_hallucinated_audio_quote_cannot_prompt_camera(self):
        called=[]
        async def capture(*a,**kw):
            called.append(True)
        native,sent=self.make_native(capture)
        native.state.begin_turn('ordinary-audio',audio=True)
        native.state.user_transcript('ordinary-audio','מה קורה מיקו?')
        result=self.run_async(native.observe_camera({'call_id':'c1','arguments':{
            'source_quote':'תסתכל במצלמה','question':'מה רואים?'}},0,'ordinary-audio'))
        self.assertEqual(result['status'],'camera_request_not_grounded')
        self.assertFalse(called)

    def test_camera_negation_cannot_prompt_camera(self):
        called=[]
        async def capture(*a,**kw):
            called.append(True)
        native,sent=self.make_native(capture)
        native.state.begin_turn('no-camera',audio=False,text='אל תשתמש במצלמה')
        result=self.run_async(native.observe_camera({'call_id':'c1','arguments':{
            'source_quote':'אל תשתמש במצלמה','question':'מה רואים?'}},0,'no-camera'))
        self.assertEqual(result['status'],'camera_request_not_grounded')
        self.assertFalse(called)

    def test_response_created_after_new_ptt_is_canceled_and_never_played(self):
        outbound=[]; rendered=[]
        async def send(raw):
            rendered.append(json.loads(raw))
        native=NativeSession(self.hub,SimpleNamespace(send=send))
        async def api_send(event):
            outbound.append(event)
        native.api_send=api_send
        native.state.begin_turn('old',audio=False,text='שאלה ישנה')
        self.run_async(native.request_response())
        metadata=outbound[-1]['response']['metadata']
        self.run_async(native.interrupt({}))
        native.state.begin_turn('new',audio=True)
        class API:
            def __aiter__(self):
                async def events():
                    for event in [
                        {'type':'response.created','response':{'id':'late','metadata':metadata}},
                        {'type':'response.output_audio.delta','response_id':'late','item_id':'old-output','delta':'AAA='},
                        {'type':'response.output_audio_transcript.done','response_id':'late','item_id':'old-output','transcript':'תשובה ישנה'},
                        {'type':'response.done','response':{'id':'late','status':'completed','output':[]}},
                    ]: yield json.dumps(event)
                return events()
        self.run_async(native.api_events(API()))
        self.assertTrue(any(e['type']=='response.cancel' and e.get('response_id')=='late' for e in outbound))
        self.assertFalse(any(e['type'] in ('audio','transcript') for e in rendered))
        self.assertEqual(native.state.turn_id,'new')

    def test_late_commit_uses_frozen_ptt_turn(self):
        native=NativeSession(self.hub,SimpleNamespace())
        native.state.begin_turn('old',audio=True)
        native.pending_commits.append('old')
        native.state.begin_turn('new',audio=True)
        async def send(event): pass
        native.send=send
        class API:
            def __aiter__(self):
                async def events():
                    yield json.dumps({'type':'input_audio_buffer.committed','item_id':'old-item'})
                    yield json.dumps({'type':'conversation.item.input_audio_transcription.completed','item_id':'old-item','transcript':'שאלה ישנה'})
                return events()
        self.run_async(native.api_events(API()))
        self.assertEqual(native.state.input_item_turns['old-item'],'old')
        self.assertEqual(native.state.last_user,'')

    def test_native_transcript_arrives_after_stop_without_another_start(self):
        """The relay must push a late STT result without another PTT press."""
        async def scenario():
            client_events = []
            model_events = []

            async def send_client(raw):
                client_events.append(json.loads(raw))

            class Model:
                def __init__(self):
                    self.incoming = asyncio.Queue()

                async def send(self, raw):
                    model_events.append(json.loads(raw))

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    event = await self.incoming.get()
                    if event is None:
                        raise StopAsyncIteration
                    return json.dumps(event)

            native = NativeSession(self.hub, SimpleNamespace(send=send_client))
            model = Model()
            native.api = model
            reader = asyncio.create_task(native.api_events(model))
            try:
                await native.client_event({'type':'start'})
                pcm = b'\x21\x00' * 4800
                await native.client_event({'type':'audio','audio':base64.b64encode(pcm).decode('ascii')})
                await native.client_event({'type':'stop'})
                turn = next(e for e in client_events if e['type']=='turn_started')
                self.assertEqual(sum(e['type']=='input_audio_buffer.commit' for e in model_events),1)

                # STT can complete after the user has released SPACE, even after
                # the model has begun answering. There is no second client start.
                await model.incoming.put({'type':'input_audio_buffer.committed','item_id':'spoken-item'})
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.completed',
                                          'item_id':'spoken-item','transcript':'Synthetic voice question'})
                for _ in range(100):
                    if any(e['type']=='transcript' for e in client_events):
                        break
                    await asyncio.sleep(.001)
                else:
                    self.fail('Late STT transcript was not pushed after PTT release')
                spoken = next(e for e in client_events if e['type']=='transcript')
                self.assertEqual((spoken['role'],spoken['text']),('user','Synthetic voice question'))
                self.assertEqual((spoken['turn_id'],spoken['turn_number'],spoken['session_id']),
                                 (turn['turn_id'],turn['turn_number'],turn['session_id']))
                self.assertEqual(sum(e['type']=='turn_started' for e in client_events),1)
            finally:
                await model.incoming.put(None)
                await reader

        self.run_async(scenario())

    def test_native_ptt_appends_all_bursted_audio_before_commit(self):
        """A delayed connection may deliver a whole held-SPACE recording at once."""
        async def scenario():
            model_events = []
            async def send_model(raw):
                model_events.append(json.loads(raw))

            native = NativeSession(self.hub, SimpleNamespace(send=lambda _raw: asyncio.sleep(0)))
            native.api = SimpleNamespace(send=send_model)
            await native.client_event({'type':'start'})
            # Ten distinct 100-ms PCM chunks detect a dropped middle or tail.
            chunks = [bytes([index,0]) * 2400 for index in range(10)]
            for chunk in chunks:
                await native.client_event({'type':'audio','audio':base64.b64encode(chunk).decode('ascii')})
            await native.client_event({'type':'stop'})

            kinds = [event['type'] for event in model_events]
            append_indices = [i for i,kind in enumerate(kinds) if kind=='input_audio_buffer.append']
            commit = kinds.index('input_audio_buffer.commit')
            response = kinds.index('response.create')
            self.assertEqual(len(append_indices),len(chunks))
            self.assertTrue(all(index < commit for index in append_indices))
            self.assertLess(commit,response)
            self.assertEqual(
                b''.join(base64.b64decode(model_events[index]['audio']) for index in append_indices),
                b''.join(chunks))
            self.assertEqual(native.input_bytes,0)

        self.run_async(scenario())

    def test_native_user_caption_streams_and_final_corrects_old_turn(self):
        """Partial STT is prompt display, while final STT alone is trusted."""
        async def scenario():
            client_events = []
            model_events = []

            async def send_client(raw):
                client_events.append(json.loads(raw))

            class Model:
                def __init__(self):
                    self.incoming = asyncio.Queue()

                async def send(self, raw):
                    model_events.append(json.loads(raw))

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    event = await self.incoming.get()
                    if event is None:
                        raise StopAsyncIteration
                    return json.dumps(event)

            async def captions(count):
                for _ in range(100):
                    seen = [e for e in client_events if e['type']=='transcript']
                    if len(seen) >= count:
                        return seen
                    await asyncio.sleep(.001)
                self.fail('Caption did not reach the client without another PTT press')

            native = NativeSession(self.hub, SimpleNamespace(send=send_client))
            model = Model()
            native.api = model
            reader = asyncio.create_task(native.api_events(model))
            try:
                await native.client_event({'type':'start'})
                old_turn = next(e for e in client_events if e['type']=='turn_started')
                await native.client_event({'type':'audio',
                    'audio':base64.b64encode(b'\x21\x00' * 4800).decode('ascii')})
                await native.client_event({'type':'stop'})
                self.assertTrue(any(e['type']=='input_audio_buffer.commit' for e in model_events))
                await model.incoming.put({'type':'input_audio_buffer.committed','item_id':'spoken-old'})
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.delta',
                                          'item_id':'spoken-old','delta':'Syn'})
                first = (await captions(1))[0]
                self.assertEqual((first['text'],first['final']),('Syn',False))
                self.assertEqual(first['turn_id'],old_turn['turn_id'])
                self.assertEqual(native.state.last_user,'')
                self.assertEqual(native.state.tools._user_text,'')
                self.assertEqual(self.brain.miko.get('realtime_conversation_history',[]),[])
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.delta',
                                          'item_id':'spoken-old','delta':'thetic misheard'})
                second = (await captions(2))[1]
                self.assertEqual(second['text'],'Synthetic misheard')

                # A new press must not reassign late caption chunks from the
                # already committed older input item.
                await native.client_event({'type':'start'})
                new_turn = [e for e in client_events if e['type']=='turn_started'][-1]
                self.assertNotEqual(old_turn['turn_id'],new_turn['turn_id'])
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.delta',
                                          'item_id':'spoken-old','delta':' ending'})
                third = (await captions(3))[2]
                self.assertEqual((third['text'],third['turn_id']),
                                 ('Synthetic misheard ending',old_turn['turn_id']))
                self.assertEqual(native.state.last_user,'')
                self.assertEqual(native.state.tools._user_text,'')

                await model.incoming.put({'type':'conversation.item.input_audio_transcription.completed',
                                          'item_id':'spoken-old','transcript':'Synthetic corrected sentence'})
                final = (await captions(4))[3]
                self.assertEqual((final['text'],final['final'],final['turn_id']),
                                 ('Synthetic corrected sentence',True,old_turn['turn_id']))
                self.assertEqual(native.state.last_user,'')
                self.assertEqual(native.state.tools._user_text,'')
                saved = self.brain.miko.get('realtime_conversation_history',[])
                self.assertEqual([(e['text'],e['turn_id']) for e in saved],
                                 [('Synthetic corrected sentence',old_turn['turn_id'])])
                await model.incoming.put({'type':'conversation.item.input_audio_transcription.delta',
                                          'item_id':'spoken-old','delta':' stale'})
                await asyncio.sleep(.01)
                self.assertEqual(len([e for e in client_events if e['type']=='transcript']),4)
            finally:
                await model.incoming.put(None)
                await reader

        self.run_async(scenario())

    def test_native_assistant_caption_is_not_trusted_output_before_final(self):
        async def scenario():
            client_events = []
            model_events = []

            async def send_client(raw):
                client_events.append(json.loads(raw))

            class Model:
                def __init__(self):
                    self.incoming = asyncio.Queue()

                async def send(self, raw):
                    model_events.append(json.loads(raw))

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    event = await self.incoming.get()
                    if event is None:
                        raise StopAsyncIteration
                    return json.dumps(event)

            async def wait_for(predicate):
                for _ in range(100):
                    if predicate():
                        return
                    await asyncio.sleep(.001)
                self.fail('Realtime event was not handled promptly')

            native = NativeSession(self.hub, SimpleNamespace(send=send_client))
            model = Model()
            native.api = model
            reader = asyncio.create_task(native.api_events(model))
            try:
                await native.client_event({'type':'start'})
                old_turn = next(e for e in client_events if e['type']=='turn_started')
                await native.client_event({'type':'audio',
                    'audio':base64.b64encode(b'\x11\x00' * 4800).decode('ascii')})
                await native.client_event({'type':'stop'})
                request = next(e for e in model_events if e['type']=='response.create')
                await model.incoming.put({'type':'response.created','response':{
                    'id':'response-old','metadata':request['response']['metadata']}})
                await model.incoming.put({'type':'response.output_audio_transcript.delta',
                                          'response_id':'response-old','item_id':'answer-old','delta':'Al'})
                await wait_for(lambda: len([e for e in client_events if e['type']=='transcript']) >= 1)
                await model.incoming.put({'type':'response.output_audio_transcript.delta',
                                          'response_id':'response-old','item_id':'answer-old','delta':'most'})
                await wait_for(lambda: len([e for e in client_events if e['type']=='transcript']) >= 2)
                partials = [e for e in client_events if e['type']=='transcript']
                self.assertEqual([(e['text'],e['final'],e['turn_id']) for e in partials],
                                 [('Al',False,old_turn['turn_id']),
                                  ('Almost',False,old_turn['turn_id'])])
                self.assertEqual(native.state.output_text,{})
                self.assertEqual(self.brain.miko.get('realtime_conversation_history',[]),[])
                self.assertIsNone(native.state.awaiting_draft)

                await model.incoming.put({'type':'response.done','response':{
                    'id':'response-old','status':'completed','output':[]}})
                await wait_for(lambda: any(e['type']=='response_done' for e in client_events))
                await native.client_event({'type':'start'})
                await model.incoming.put({'type':'response.output_audio_transcript.done',
                                          'response_id':'response-old','item_id':'answer-old',
                                          'transcript':'Correct answer'})
                await wait_for(lambda: len([e for e in client_events if e['type']=='transcript']) >= 3)
                final = [e for e in client_events if e['type']=='transcript'][-1]
                self.assertEqual((final['text'],final['final'],final['turn_id']),
                                 ('Correct answer',True,old_turn['turn_id']))
                self.assertEqual(native.state.output_text['answer-old'],'Correct answer')
                self.assertEqual(self.brain.miko.get('realtime_conversation_history',[]),[])
                await model.incoming.put({'type':'response.output_audio_transcript.delta',
                                          'response_id':'response-old','item_id':'answer-old','delta':' stale'})
                await asyncio.sleep(.01)
                self.assertEqual(len([e for e in client_events if e['type']=='transcript']),3)
            finally:
                await model.incoming.put(None)
                await reader

        self.run_async(scenario())


if __name__ == '__main__':
    fixture.SOURCE = Path(__file__).resolve().parent.parent/'miko_brain.py'
    unittest.main(verbosity=2)
