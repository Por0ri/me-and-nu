const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const root = path.join(__dirname, '..');
const modules = new Map();
function load(relative) {
  if (modules.has(relative)) return modules.get(relative).exports;
  if (relative.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'));
  const mod = { exports: {} };
  modules.set(relative, mod);
  const code = ts.transpileModule(fs.readFileSync(path.join(root, relative), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  vm.runInNewContext(code, { module: mod, exports: mod.exports, setTimeout, clearTimeout, URL, URLSearchParams, AbortController, process:{env:{}}, require(name) {
    if (name === 'react' || name === 'react/jsx-runtime') return require(name);
    if (name.endsWith('.module.css')) return { __esModule: true, default: new Proxy({}, { get: (_, key) => String(key) }) };
    if (name.startsWith('@/')) return load(name.slice(2) + (name.endsWith('.json') ? '' : '.ts'));
    if (name.startsWith('./')) {
      const base = path.join(path.dirname(relative), name);
      return load(base + (fs.existsSync(path.join(root, base + '.tsx')) ? '.tsx' : '.ts'));
    }
    throw new Error('Unexpected onboarding UI dependency: ' + name);
  } });
  return mod.exports;
}
const { OnboardingFlow, InterestScreen } = load('components/pivot/onboarding-screens.tsx');
const { PivotFrame } = load('components/pivot/pivot-ui.tsx');
const { OnboardingProfileScreen } = load('components/pivot/onboarding-profile.tsx');
const { initialOnboardingState } = load('lib/pivot/onboarding.ts');
function render(step, mode, answers = {}) {
  return renderToStaticMarkup(React.createElement(OnboardingFlow, {
    state: { ...initialOnboardingState(), step, interest: 'animation', topicId:83,
      favoriteWorks:[{entityId:101,name:'테스트 작품',aliases:[],imageUrl:null,releaseYear:2024,mediaFormat:'TV',selectable:true,unavailableReason:null}],
      preferenceOptions:[{tagId:201,kind:'genre',code:'fantasy',label:'판타지',sourceEntityIds:[101]},{tagId:301,kind:'theme',code:'friendship',label:'우정',sourceEntityIds:[101]}],
      preferenceTagIds:[201,301],optionsKey:'101',consumptionType:'ost',...answers },
    dispatch() {}, onExit() {}, mode,
  }));
}
test('My Page 관심분야 관리: actual subscription state, back button, no checkbox or start button', () => {
  for (const selected of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(InterestScreen, { selected, onToggle() {}, onNext() {}, busy: false, manage: { onBack() {} } }));
    assert.match(html, /aria-label="뒤로가기"/);
    assert.match(html, selected ? />선택중</ : />미가입</);
    assert.match(html, selected ? /이미 애니메이션을 탐색중입니다/ : /가입된 애니메이션 관심 분야가 없어요\./);
    assert.doesNotMatch(html, selected ? /미가입|가입된 애니메이션 관심 분야가 없어요/ : /선택중|이미 애니메이션을 탐색중입니다/);
    assert.doesNotMatch(html, /type="checkbox"|애니메이션 선택하고 시작하기/);
    assert.match(html, /alt="me;nu" width="103"/);
    assert.match(html, /Little discoveries<br\/>for a more you/);
    assert.match(html, /aria-label="알림"/);
  }
  const onboarding = renderToStaticMarkup(React.createElement(InterestScreen, { selected: true, onToggle() {}, onNext() {}, busy: false }));
  assert.match(onboarding, /선택 완료/); assert.match(onboarding, /애니메이션 선택하고 시작하기/); assert.doesNotMatch(onboarding, /뒤로가기/);
});
for (const mode of ['demo', 'service']) {
  test(mode + ': interest/result omit ordinary Back; questions retain it', () => {
    for (const step of ['interest', 'result']) assert.doesNotMatch(render(step, mode), /aria-label="이전 단계"/);
    for (const step of ['q1', 'q2','q3']) assert.equal((render(step, mode).match(/aria-label="이전 단계"/g) || []).length, 1);
    assert.match(render('result', mode), />다시 선택하기<\/button>/);
  });
  test(mode + ': Q1 selected works and every result chip row slide with a mouse drag', () => {
    assert.match(render('q1', mode), /<ul class="selectedWorks" aria-label="선택한 작품" data-drag-scroll="">/);
    assert.equal((render('result', mode).match(/<ul class="summaryChips" data-drag-scroll="">/g) || []).length, 4);
  });
  test(mode + ': Q2 explains the per-kind minimum and an incomplete result cannot be submitted', () => {
    assert.match(render('q2', mode), /장르·소재 각각 최소 1개/);
    for (const ids of [[], [201], [301]]) {
      const html = render('result', mode, {preferenceTagIds: ids});
      assert.match(html, /<button[^>]*disabled=""[^>]*aria-describedby="home-feedback"/);
    }
  });
  test(mode + ': unanswered Q1/Q2/Q3 block CTA and contain no stale result summary', () => {
    for (const step of ['q1', 'q2','q3']) {
      const html = render(step, mode, { favoriteWorks:[],preferenceTagIds:[],preferenceOptions:[],optionsKey:null,consumptionType:null });
      assert.match(html, /<button[^>]*disabled=""[^>]*>(?:선택 완료|다음|장르·소재 선택 완료)<\/button>/);
      assert.doesNotMatch(html, /선택 답변 요약/);
      assert.match(html,/aria-valuemax="3"/);
    }
  });
}
test('Web frame omits simulated OS status indicators even in design preview', () => {
  const html = renderToStaticMarkup(React.createElement(PivotFrame, { chrome: true }, 'Screen'));
  assert.doesNotMatch(html, /9:41|imgWifi|imgOutline|imgIconMobileSignal|imgBatteryEnd/);
});

test('Deferred birth-date policy exposes only the nickname input in the new onboarding profile',()=>{
  const html=renderToStaticMarkup(React.createElement(OnboardingProfileScreen,{profile:{nickname:''},onChange(){},onContinue(){}}));
  assert.match(html,/name="nickname"/);
  assert.doesNotMatch(html,/birthDate|type="date"|만 14세|autoComplete="bday"/);
});

test('Q2 spreads the 16 genres over three lines and keeps the catalogue order within each line',()=>{
  const { balancedRows } = load('components/pivot/onboarding-catalog-screens.tsx');
  const labels=['코미디','액션','드라마','로맨스','SF','판타지','일상','모험','초자연','로봇','미스터리','심리','스포츠','호러','음악','스릴러'];
  const rows=balancedRows(labels.map((label,at)=>({label,at})),3);
  assert.equal(rows.length,3);
  assert.deepEqual(Array.from(rows.flat(),tag=>tag.label).sort(),[...labels].sort());
  for (const row of rows) {
    const order=Array.from(row,tag=>tag.at);
    assert.deepEqual(order,[...order].sort((a,b)=>a-b));
    assert.ok(row.length>=5&&row.length<=6,`${row.length} chips in one line`);
  }
});

test('Q2 box shows the chips the chosen works carry, most shared first, three per kind',()=>{
  const { discoveredTags } = load('components/pivot/onboarding-catalog-screens.tsx');
  const tag=(tagId,kind,ids)=>({tagId,kind,code:String(tagId),label:'t'+tagId,sourceEntityIds:ids});
  const options=[tag(1,'genre',[101]),tag(2,'genre',[]),tag(3,'genre',[101,102]),tag(4,'genre',[102]),tag(5,'genre',[101]),tag(6,'theme',[101])];
  assert.deepEqual(Array.from(discoveredTags(options,'genre'),t=>t.tagId),[3,1,4]);
  assert.deepEqual(Array.from(discoveredTags(options,'theme'),t=>t.tagId),[6]);
  assert.equal(discoveredTags(options.map(t=>({...t,sourceEntityIds:[]})),'genre').length,0);
});

test('Feed loading screen uses the teammate artwork with the design words',()=>{
  const { FeedLoadingScreen } = load('components/pivot/feed-loading-screen.tsx');
  const html=renderToStaticMarkup(React.createElement(FeedLoadingScreen));
  assert.match(html,/취향을 불러오는 중/);
  assert.match(html,/맞춤 콘텐츠를 수집하고 있어요/);
  assert.match(html,/\/pivot\/loading\/phase-1\.png/);
  assert.match(html,/\/pivot\/loading\/dots-1\.svg/);
  assert.doesNotMatch(html,/시연용|처음부터 시연/);
});

test('Nickname screen waits for the duplicate check and shows a taken nickname in red',()=>{
  const screen=props=>renderToStaticMarkup(React.createElement(OnboardingProfileScreen,{onChange(){},onContinue(){},...props}));
  // A valid nickname still waits (grey) until the check answers; without a check it can start at once.
  assert.match(screen({profile:{nickname:'미뉴덕후'},checkNickname:async()=>({available:true,reason:null})}),/disabled=""[^>]*>시작하기/);
  assert.doesNotMatch(screen({profile:{nickname:'미뉴덕후'}}),/disabled=""/);
  const taken=screen({profile:{nickname:'공룡'},message:'중복된 닉네임입니다'});
  assert.match(taken,/role="alert">중복된 닉네임입니다</);
  assert.match(taken,/aria-invalid="true"/);
});

test('Nickname screen marks each broken part of the 2~20 rule and starts only with a valid nickname',()=>{
  const render=nickname=>renderToStaticMarkup(React.createElement(OnboardingProfileScreen,{profile:{nickname},onChange(){},onContinue(){}}));
  const red=part=>new RegExp(`<span class="profileRuleBroken">${part}</span>`);
  const empty=render('');
  assert.match(empty.replace(/<[^>]+>/g,''),/2~20자, 공백·특수문자 제외/);
  assert.match(empty,/>0\/20</);
  assert.match(empty,/<button type="submit"[^>]*disabled=""[^>]*>시작하기<\/button>/);
  assert.doesNotMatch(empty,/profileRuleBroken|약관으로 돌아가기|취향 선택하기|1~30자/);
  const valid=render('미뉴덕후');
  assert.match(valid,/>4\/20</);
  assert.doesNotMatch(valid,/disabled=""|profileRuleBroken/);
  for (const [nickname,parts] of [['미',['2~20자']],['가'.repeat(21),['2~20자']],['미뉴 덕후',['공백·특수문자 제외']],['킹갓디노 ',['공백·특수문자 제외']],['!',['2~20자','공백·특수문자 제외']]]) {
    const html=render(nickname);
    assert.match(html,/disabled=""/,nickname);
    for (const part of ['2~20자','공백·특수문자 제외']) (parts.includes(part)?assert.match:assert.doesNotMatch)(html,red(part),`${nickname}: ${part}`);
  }
});

