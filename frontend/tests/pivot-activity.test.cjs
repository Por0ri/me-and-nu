const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const test=require('node:test'),assert=require('node:assert/strict'),ts=require('typescript');
function load(file,api={}) {
 const mod={exports:{}};
 const output=ts.transpileModule(fs.readFileSync(path.join(__dirname,'..',file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(output,{module:mod,exports:mod.exports,AbortController,setTimeout,clearTimeout,Date,crypto:require('node:crypto').webcrypto,
  require(name){if(name==='@/lib/api')return api;if(name==='./services')return {requirePivotApi(){}};if(name==='./activity')return load('lib/pivot/activity.ts');throw Error(name);}});
 return mod.exports;
}
const activity=load('lib/pivot/activity.ts'),target={postId:'41',contentId:'41',topicId:7,recommendationId:9};
const session=id=>({authenticated:true,sessionState:'active',onboardingCompleted:true,user:{id,accountType:'user'},csrfToken:'csrf-'+id});
function setup(options={}) {
 let current=session(12),verified=current,sequence=0;
 const calls=[],timers=new Map();let next=0;
 const queue=activity.createActivityQueue(12,()=>current,{allow(){},verifySession:async()=>verified,
  post:async(events,init)=>{calls.push({events:JSON.parse(JSON.stringify(events)),init});return {saved:events.length,duplicated:0,rejected:0};},
  uuid:()=>`00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`,now:()=>100000,
  schedule:(fn,delay)=>{const key=++next;timers.set(key,{fn,delay});return key;},cancel:key=>timers.delete(key),...options});
 return {queue,calls,timers,setCurrent:s=>current=s,setVerified:s=>verified=s};
}
test('activity batches use camelCase API, stable session IDs, unique UUIDs, and recommendation item IDs',async()=>{
 const s=setup();s.queue.record(target,'impression',{surface:'home_feed',visibleMs:1200});s.queue.record(target,'open',{surface:'detail',activeMs:3000,scrollPct:75});
 await s.queue.flush();assert.equal(s.calls.length,1);const [a,b]=s.calls[0].events;
 assert.equal(s.calls[0].init.headers["X-CSRF-Token"],"csrf-12");assert.equal(a.recommendationItemId,9);assert.equal(a.topicId,7);assert.equal(a.contentId,41);assert.equal(a.value.sessionId,b.value.sessionId);assert.notEqual(a.clientEventId,b.clientEventId);assert.equal(a.value.visibleMs,1200);assert.ok(a.occurredAt.endsWith('Z'));s.queue.stop();
});
test('activity transient retry resends exact UUIDs and keeps new events behind the original batch',async()=>{
 let tries=0;const batches=[];const s=setup({post:async(events)=>{batches.push(JSON.parse(JSON.stringify(events)));if(++tries===1)throw Object.assign(Error('offline'),{status:503});return {saved:0,duplicated:events.length,rejected:0};}});
 s.queue.record(target,'open',{surface:'detail'});await s.queue.flush();s.queue.record(target,'card_swipe',{surface:'home_feed',cardIndex:2,cardCount:3});await s.queue.flush();
 assert.equal(batches[0][0].clientEventId,batches[1][0].clientEventId);assert.equal(batches[1].length,2);await s.queue.flush();assert.equal(batches.length,2);s.queue.stop();
});
test('401/403 discard queued activity and do not retry under another login',async()=>{
 for(const status of [401,403]){let calls=0;const s=setup({post:async()=>{calls++;throw {status};}});s.queue.record(target,'open',{surface:'detail'});await s.queue.flush();assert.equal(s.queue.record(target,'open',{surface:'detail'}),false);await s.queue.flush();assert.equal(calls,1);assert.equal(s.timers.size,0);}
});
test('account mismatch before sending clears queue without posting or leaking metadata',async()=>{
 const s=setup();s.queue.remember(target);s.queue.record(target,'open',{surface:'detail'});s.setVerified(session(99));await s.queue.flush();assert.equal(s.calls.length,0);assert.equal(s.queue.metadata(target),undefined);assert.equal(s.queue.record(target,'open',{surface:'detail'}),false);
});
test('local session expiration prevents any queued post',async()=>{
 const s=setup();s.queue.record(target,'open',{surface:'detail'});s.setCurrent(null);await s.queue.flush();assert.equal(s.calls.length,0);assert.equal(s.timers.size,0);
});
test('logout aborts in-flight activity and its late acknowledgement cannot consume the next scope',async()=>{
 let release,signal;const s=setup({post:async(events,init)=>{signal=init.signal;return new Promise(resolve=>{release=()=>resolve({saved:events.length,duplicated:0,rejected:0});});}});
 s.queue.record(target,'open',{surface:'detail'});const pending=s.queue.flush();await new Promise(setImmediate);s.queue.stop();assert.equal(signal.aborted,true);release();await pending;assert.equal(s.timers.size,0);
});
test('batch size and memory cap are bounded, retry cannot grow the queue without limit',async()=>{
 const s=setup();for(let i=0;i<250;i++)s.queue.record(target,'open',{surface:'detail'});
 for(let i=0;i<10;i++)await s.queue.flush();assert.equal(s.calls.length,8);assert.ok(s.calls.every(c=>c.events.length===25));s.queue.stop();
});
test('direct/saved detail reuses known recommendation metadata without trusting arbitrary identity',async()=>{
 const s=setup();s.queue.remember(target);s.queue.remember({postId:target.postId,contentId:target.contentId,topicId:target.topicId});s.queue.record({...target,recommendationId:undefined},'open',{surface:'detail'});assert.equal(s.queue.record({...target,contentId:'1e2'},'open',{surface:'detail'}),false);await s.queue.flush(true);assert.equal(s.calls[0].events[0].recommendationItemId,9);assert.equal(s.calls[0].init.keepalive,true);s.queue.stop();
});
test('foreground measurements exclude hidden time, clamp scroll and reset completed intervals',()=>{
 let now=0;const e=activity.createExposure(()=>now);e.setVisible(true);now=1500;e.scroll(25);e.setVisible(false);now=9000;e.setVisible(true);now=9500;e.scroll(140);
 assert.deepEqual(JSON.parse(JSON.stringify(e.finish())),{duration:2000,scrollPct:100});assert.equal(e.finish().duration,0);
});
test('carousel activity ignores programmatic restoration, repeated same index, invalid and expired input',()=>{
 let now=0;const c=activity.createCarouselMeasurement(()=>now);assert.equal(c.move(2,3),false);c.interact();assert.equal(c.move(1,3),true);assert.equal(c.move(1,3),false);assert.equal(c.move(3,3),false);now=7000;assert.equal(c.move(0,3),false);
});
test('activity transport targets the authenticated batch endpoint with CSRF-capable API client',async()=>{
 let request;const {postActivityEvents}=load('lib/pivot/activity.ts',{apiRequest:async(...args)=>{request=args;return {saved:1,duplicated:0,rejected:0};}});const signal=new AbortController().signal;
 await postActivityEvents([{clientEventId:'test'}],{signal,keepalive:true});assert.equal(request[0],'/api/v1/users/me/activity-events');assert.equal(request[1].method,'POST');assert.equal(request[1].signal,signal);assert.equal(request[1].keepalive,true);assert.deepEqual(JSON.parse(request[1].body),{events:[{clientEventId:'test'}]});
});
test('reactions wait for success, dedupe concurrent writes, share state and only then record learning events',async()=>{
 const {createReactionControls}=load('lib/pivot/reaction-service.ts');let release,calls=0;const events=[];
 const scope={assertOwner(){},authorize:async()=>({headers:{"X-CSRF-Token":"scope-csrf"}}),record(...args){events.push(args);}};
 const r=createReactionControls(scope,{likeContent:async(id)=>{calls++;return new Promise(resolve=>release=()=>resolve({contentId:id,liked:true}));},unlikeContent:async()=>{},setPreference:async(id,topic,value)=>({contentId:id,topicId:topic,value}),clearPreference:async()=>{}});
 const first=r.toggleLike(target,{liked:false},'home_feed');assert.equal(r.toggleLike(target,{liked:false},'detail'),first);await new Promise(setImmediate);assert.equal(calls,1);assert.equal(r.value(target,{liked:false}).liked,false);assert.equal(events.length,0);release();await first;assert.equal(r.value(target).liked,true);assert.equal(events[0][1],'like');await r.toggleLike(target,{liked:true},'detail');assert.equal(r.value(target).liked,false);assert.equal(events[1][1],'like_cancel');
 await r.choosePreference(target,{},'positive','detail');assert.equal(r.value(target).preference,'positive');await r.choosePreference(target,{},'positive','detail');assert.equal(r.value(target).preference,null);assert.equal(events.at(-1)[1],'preference_cancel');r.clear();
});
test('failed/late reaction responses never announce success or produce activity',async()=>{
 const {createReactionControls}=load('lib/pivot/reaction-service.ts');const events=[];let release;
 const r=createReactionControls({assertOwner(){},authorize:async()=>({headers:{"X-CSRF-Token":"scope-csrf"}}),record(...args){events.push(args);}},{likeContent:async()=>{throw Error('offline');},setPreference:async(id,topic,value)=>new Promise(resolve=>release=()=>resolve({contentId:id,topicId:topic,value}))});
 await assert.rejects(r.toggleLike(target,{liked:false},'detail'),/offline/);assert.equal(r.value(target,{liked:false}).liked,false);assert.equal(events.length,0);
 const pending=r.choosePreference(target,{},'negative','detail');await new Promise(setImmediate);r.clear();release();await assert.rejects(pending);assert.equal(events.length,0);assert.equal(r.value(target).preference,undefined);
});

test('page exit sends keepalive with the pinned owner token without a cancellable session GET',async()=>{
 const s=setup({verifySession:async()=>{throw Error('GET should not run on pagehide');}});s.queue.record(target,'open',{surface:'detail',activeMs:1200});await s.queue.flush(true);
 assert.equal(s.calls.length,1);assert.equal(s.calls[0].init.headers['X-CSRF-Token'],'csrf-12');assert.equal(s.calls[0].init.keepalive,true);s.queue.stop();
});
test('an updated non-recommended feed card clears old recommendation metadata',()=>{
 const s=setup();s.queue.remember(target);s.queue.remember({...target,recommendationId:undefined,recommendationReason:undefined});assert.equal(s.queue.metadata(target).recommendationId,undefined);s.queue.stop();
});

test('logout drain sends every acknowledged batch but stops at the first failure',async()=>{
 const s=setup();for(let i=0;i<60;i++)s.queue.record(target,'open',{surface:'detail'});await s.queue.drain();assert.deepEqual(s.calls.map(call=>call.events.length),[25,25,10]);s.queue.stop();
 let calls=0;const failure=setup({post:async()=>{calls++;throw {status:503};}});for(let i=0;i<60;i++)failure.queue.record(target,'open',{surface:'detail'});await failure.queue.drain();assert.equal(calls,1);failure.queue.stop();
});
