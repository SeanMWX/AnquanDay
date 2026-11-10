'use strict';
const $=id=>document.getElementById(id),fmt=new Intl.NumberFormat('zh-CN');
let DATA={sources:[]},sources=new Map();
const state={source:'all',page:1,pageSize:12,query:'',filter:'all',sort:'published',zone:'Europe/Brussels',date:null,month:null,followToday:true};
let overview=null,calendarData=null,listData=null,ready=false,controller=null,requestId=0;
const labels={article_body:'已取得正文',feed_content:'来源正文',rss_excerpt:'仅来源摘要',title_only:'仅标题'};
const displayTitle=a=>a.display_title||'标题待翻译';
const sourceName=s=>s?.name||'其他来源';
const translationLabel=a=>a.translation?.status==='original_zh'?'中文原文':a.translation?.status==='translated'?'中文译文':'等待翻译';
const timeLabels={explicit_timezone:'来源明确提供时区',source_timezone_assumed:'时区为推定值',timezone_unknown:'时区未知',missing:'来源未提供',date_only:'仅精确到日期',ambiguous_local_time:'夏令时歧义',nonexistent_local_time:'不存在的本地时间',unresolved:'尚未解析'};
const errorLabels={http_403:'文章页返回 HTTP 403',http_405:'来源返回 HTTP 405',http_521:'文章页返回 HTTP 521',http_429:'请求受到限流（429）',body_requests_paused_after_repeated_block:'该来源连续失败，已暂停后续正文请求',body_selector_missing_or_too_short:'未提取到足够正文',all_endpoints_failed:'来源端点不可用'};
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
function safeUrl(value){try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)?u.href:null;}catch{return null;}}
function link(text,url){const a=el('a','',text),u=safeUrl(url);if(u){a.href=u;a.target='_blank';a.rel='noopener noreferrer';}return a;}
function dateParts(value){if(!value)return null;const d=new Date(value);if(Number.isNaN(d.valueOf()))return null;const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:state.zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d);const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));return {day:p.year+'-'+p.month+'-'+p.day,time:p.hour+':'+p.minute};}
function dateText(value){const p=dateParts(value);return p?p.day+' '+p.time:'未提供';}
function todayKey(now=new Date()){return dateParts(now.toISOString()).day;}
function monthCells(month){const [year,m]=month.split('-').map(Number),start=(new Date(Date.UTC(year,m-1,1)).getUTCDay()+6)%7,days=new Date(Date.UTC(year,m,0)).getUTCDate();return [...Array(start).fill(null),...Array.from({length:days},(_,i)=>month+'-'+String(i+1).padStart(2,'0'))];}
function metadataField(label,value,wide=false,mono=false){const d=el('div',wide?'wide':'');d.append(el('dt','',label));const dd=el('dd',mono?'mono':'');if(value instanceof Node)dd.append(value);else dd.textContent=value===null||value===undefined||value===''?'未提供':String(value);d.append(dd);return d;}
function newsDetails(a){const box=el('div','detail-content');box.append(el('h3','detail-heading','文章原始信息 · #'+a.id));const dl=el('dl','metadata');const fields=[['标题（原文）',a.title,true],['标题（中文）',a.title_zh||'等待翻译',true],['翻译状态',translationLabel(a)],['翻译模型',a.translation?.model||'未使用'],['翻译时间',a.translation?.translated_at?dateText(a.translation.translated_at):'未翻译'],['来源 / 文章 ID',a.source_id+' / '+a.id],['原文语言',a.language==='zh'?'中文':a.language||'未提供'],['内容版本',a.revision_count+' 个版本'],['来源原始发布时间',a.published?.raw],['标准 UTC 时间',a.published?.utc],['时区状态',timeLabels[a.published?.status]||a.published?.status],['首次发现',dateText(a.first_seen_at)],['最近采集',dateText(a.last_seen_at)],['来源明确更新时间',a.modified?.utc||'来源未提供'],['事件发生时间',a.happened_at||'尚未确认'],['内容类型',a.content_kind],['正文 / 证据 / 引用',fmt.format(a.text_length)+' 字符 · '+a.evidence_count+' 片段 · '+a.reference_count+' 链接'],['HTTP 状态 / 提取位置',(a.page_http_status||'未请求或未取得响应')+' / '+(a.selector||'—')],['原始时区字段',a.published?.source_timezone],['抓取记录 ID','RSS '+(a.feed_fetch_id||'—')+' / 页面 '+(a.page_fetch_id||'—')]];fields.forEach(f=>dl.append(metadataField(...f)));dl.append(metadataField('原文链接',link(a.url,a.url),true),metadataField('GUID',a.guid,true,true),metadataField('正文 SHA-256',a.content_sha256,true,true),metadataField('版本 SHA-256',a.revision_hash,true,true));if(a.body_error)dl.append(metadataField('正文获取说明',errorLabels[a.body_error]||a.body_error,true));box.append(dl,el('div','detail-info','这里显示真实采集信息，不展示生成式摘要。发生时间与报道时间分别保存，未确认的信息保持为空。'));return box;}
function kevDetails(a){const box=el('div','detail-content');box.append(el('h3','detail-heading','漏洞目录原始信息 · '+a.cveID));const dl=el('dl','metadata');[['漏洞名称（原文）',a.vulnerabilityName,true],['漏洞名称（中文）',a.title_zh||'等待翻译',true],['翻译状态',translationLabel(a)],['翻译模型',a.translation?.model||'未使用'],['CVE ID',a.cveID],['厂商 / 产品',a.vendorProject+' / '+a.product],['加入目录的日期',a.dateAdded],['处置截止日期',a.dueDate],['已知勒索活动使用',a.knownRansomwareCampaignUse],['首次采集',dateText(a.first_seen_at)]].forEach(f=>dl.append(metadataField(...f)));dl.append(metadataField('漏洞描述（原文）',a.shortDescription,true),metadataField('建议措施（原文）',a.requiredAction,true),metadataField('来源备注',a.notes,true),metadataField('官方目录',link('打开 CISA KEV 原始目录','https://www.cisa.gov/known-exploited-vulnerabilities-catalog'),true));box.append(dl,el('div','detail-info','加入目录的日期不代表漏洞发生时间。该记录属于基线目录，不作为当天新增新闻。'));return box;}
function renderRows(rows){const kev=state.source==='cisa_kev';$('table-head').replaceChildren();const head=el('tr');const headers=kev?['漏洞 / 产品','加入目录','勒索活动使用','目录类型','原文与详情']:['新闻 / 来源','发布时间','获取状态','内容量','原文与详情'];headers.forEach((x,i)=>head.append(el('th',['col-title','col-date','col-status','col-size','col-detail'][i],x)));$('table-head').append(head);const body=$('article-list');body.replaceChildren();rows.forEach((a,idx)=>{const tr=el('tr','article-row'),detail=el('tr','detail-row');detail.hidden=true;detail.id='meta-'+(kev?a.cveID:a.id);const title=el('td'),time=el('td'),status=el('td'),size=el('td'),action=el('td');if(kev){const headline=link(a.cveID+' · '+displayTitle(a),'https://www.cisa.gov/known-exploited-vulnerabilities-catalog');headline.className='article-title';title.append(headline,el('div','article-sub',translationLabel(a)+' · 展开查看原文'));time.append(el('div','time-main',a.dateAdded),el('div','time-sub','仅精确到日期'));status.append(el('span','status'+(a.knownRansomwareCampaignUse==='Known'?' excerpt':''),a.knownRansomwareCampaignUse==='Known'?'已知用于勒索':'未确认'));size.append(el('div','size-main','漏洞目录'),el('div','size-sub','与新闻分开统计'));}else{const headline=link(displayTitle(a),a.url);headline.className='article-title';const sub=el('div','article-sub');sub.append(el('span','',sourceName(sources.get(a.source_id))),el('span','separator','·'),el('span','lang',translationLabel(a)));title.append(headline,sub);const dt=dateParts(a.published?.utc);time.append(el('div','time-main',dt?dt.day:(a.published?.status==='date_only'?(a.published.date||a.published.raw):'时间未确定')),el('div','time-sub',dt?dt.time+(a.published.status==='source_timezone_assumed'?' · 时区推定':''):(a.published?.status==='date_only'?'仅精确到日期':'来源未提供可确定的时间')));status.append(el('span','status'+(a.content_kind==='title_only'?' missing':a.content_kind==='article_body'?'':' excerpt'),labels[a.content_kind]||'未分类'));size.append(el('div','size-main',fmt.format(a.text_length)+' 字符'),el('div','size-sub',a.evidence_count+' 个证据片段'));}const toggle=el('button','detail-btn','展开原文 ↗');toggle.type='button';toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-controls',detail.id);toggle.setAttribute('aria-label','查看 '+(kev?a.cveID:displayTitle(a))+' 的原文与详情');toggle.addEventListener('click',()=>{detail.hidden=!detail.hidden;toggle.setAttribute('aria-expanded',String(!detail.hidden));toggle.textContent=detail.hidden?'展开原文 ↗':'收起 ↑';if(!detail.hidden&&!detail.childNodes.length){const td=el('td');td.colSpan=5;td.append(kev?kevDetails(a):newsDetails(a));detail.append(td);}});action.append(toggle);tr.append(title,time,status,size,action);body.append(tr,detail);});}

function zoneName(){return state.zone==='Europe/Brussels'?'比利时':state.zone==='Asia/Shanghai'?'北京时间':state.zone==='UTC'?'协调世界时':'所选时区';}
function sourceCount(id){return id==='all'?overview?.news||0:overview?.source_counts[id]||0;}
function makeTabs(){const tabs=$('source-tabs');tabs.replaceChildren();const options=[{id:'all',name:'全部新闻'},...DATA.sources];for(const [idx,s] of options.entries()){const b=el('button','tab');b.type='button';b.id='tab-'+s.id;b.setAttribute('role','tab');b.setAttribute('aria-controls','source-panel');b.setAttribute('aria-selected',String(s.id===state.source));b.tabIndex=s.id===state.source?0:-1;b.append(el('span','',sourceName(s)),el('span','tabcount',fmt.format(sourceCount(s.id))));b.addEventListener('click',()=>selectSource(s.id));b.addEventListener('keydown',e=>{let next;if(e.key==='ArrowRight')next=(idx+1)%options.length;if(e.key==='ArrowLeft')next=(idx-1+options.length)%options.length;if(e.key==='Home')next=0;if(e.key==='End')next=options.length-1;if(next!==undefined){e.preventDefault();selectSource(options[next].id);$('tab-'+options[next].id).focus();}});tabs.append(b);}}
function selectSource(id){state.source=id;state.page=1;state.filter='all';$('content-filter').value='all';$('content-filter').disabled=id==='cisa_kev';for(const tab of $('source-tabs').children){const on=tab.id==='tab-'+id;tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;}$('source-panel').setAttribute('aria-labelledby','tab-'+id);$('source-meta').open=false;loadData();}
function sourceMetaField(label,value){const d=el('div');d.append(el('span','',label));const b=el('b');if(value instanceof Node)b.append(value);else b.textContent=value;d.append(b);return d;}
function renderSource(){const s=sources.get(state.source),all=state.source==='all',kev=state.source==='cisa_kev';$('source-title').replaceChildren(el('span','',all?'全部新闻':sourceName(s)),el('small','',fmt.format(sourceCount(state.source))+(kev?' 条目录记录':' 篇新闻')));const grid=$('source-grid');grid.replaceChildren(sourceMetaField('日期',state.date==='undated'?'日期未确定':state.date),sourceMetaField('时间显示',zoneName()),sourceMetaField('累计收集',fmt.format(all?overview.totals.articles:overview.source_totals[state.source]||0)));if(s){grid.append(sourceMetaField('原始名称',s.name),sourceMetaField('来源 ID',s.id),sourceMetaField('类型',s.kind==='kev'?'JSON 目录':'RSS'),sourceMetaField('原始入口',link(s.url,s.url)));if(s.timezone_hint)grid.append(sourceMetaField('无时区时的假设',s.timezone_hint));}}
function renderCounts(){for(const tab of $('source-tabs').children)tab.querySelector('.tabcount').textContent=fmt.format(sourceCount(tab.id.slice(4)));}
function renderHeading(){if(activeSection!=='news')return;document.title=(state.date==='undated'?'日期未确定':state.date===todayKey()?'今天':state.date)+' · AnquanDay';}
function renderCalendar(){const grid=$('calendar-grid'),counts=calendarData.days,kev=state.source==='cisa_kev',today=todayKey();grid.replaceChildren();const [year,month]=state.month.split('-');$('calendar-month').textContent=year+' 年 '+Number(month)+' 月';for(const day of monthCells(state.month)){if(!day){grid.append(el('span'));continue;}const count=counts[day]||0,b=el('button','calendar-day '+(count?'has-news':'no-news')+(day===today?' is-today':''));b.type='button';b.setAttribute('aria-label',day+'，'+count+(kev?' 条漏洞记录':' 篇新闻')+(day===today?'，今天':''));b.setAttribute('aria-pressed',String(day===state.date));if(day===today)b.setAttribute('aria-current','date');b.append(el('span','day-number',Number(day.slice(-2))),el('span','day-count',count?count+(kev?' 条':' 篇'):'—'));b.addEventListener('click',()=>setDate(day,day===todayKey()));grid.append(b);}$('undated').hidden=!calendarData.undated;$('undated').textContent='日期未确定 · '+calendarData.undated;$('calendar-note').textContent=(state.source==='all'?'全部新闻':sourceName(sources.get(state.source)))+' · '+(kev?'按加入目录日期计数':'按'+zoneName()+'日期计数')+'；数量不受搜索和状态筛选影响。空白日期表示没有收录。';}
function renderListing(){const {items,total,page,pages,page_size}=listData;state.page=page;renderRows(items);$('results-note').textContent=(state.date==='undated'?'日期未确定':state.date)+' · 显示 '+fmt.format(total)+(state.source==='cisa_kev'?' 条漏洞记录':' 篇新闻')+' · '+zoneName();const start=(page-1)*page_size;$('page-info').textContent=total?'第 '+(start+1)+'–'+Math.min(start+page_size,total)+' 条 / 共 '+fmt.format(total)+' 条':'共 0 条';$('page-number').textContent=page+' / '+pages;$('previous').disabled=page===1;$('next').disabled=page===pages;$('table-wrap').hidden=!items.length;$('empty').hidden=!!items.length;$('empty-title').textContent=state.query||state.filter!=='all'?'没有匹配的记录':'这一天暂无收录';$('empty-description').textContent='可选择其他日期或来源。未收录不代表当天没有安全事件。';}
async function getJson(path,query={},signal){const response=await fetch(path+'?'+new URLSearchParams(query),{signal,cache:'no-store'});if(!response.ok)throw new Error('HTTP '+response.status);return response.json();}
async function loadData(){if(!ready)return;const id=++requestId;controller?.abort();controller=new AbortController();const signal=controller.signal;renderHeading();$('api-message').hidden=false;$('api-message').className='api-message';$('api-message').textContent='正在读取新闻数据…';document.querySelector('.workspace').setAttribute('aria-busy','true');const base={date:state.date,tz:state.zone};try{const results=await Promise.all([getJson('/api/v1/overview',base,signal),getJson('/api/v1/calendar',{month:state.month,tz:state.zone,source:state.source},signal),getJson(state.source==='cisa_kev'?'/api/v1/kev':'/api/v1/articles',{...base,source:state.source,q:state.query,kind:state.filter,sort:state.sort,page:state.page,page_size:state.pageSize},signal)]);if(id!==requestId)return;[overview,calendarData,listData]=results;renderCounts();renderSource();renderCalendar();renderListing();$('api-message').hidden=true;}catch(error){if(id!==requestId||error.name==='AbortError')return;$('api-message').className='api-message error';$('api-message').textContent='暂时无法读取新闻数据（'+error.message+'）。请确认收集服务已启动，再刷新页面重试。';$('table-wrap').hidden=true;$('empty').hidden=true;$('page-info').textContent='数据读取失败';}finally{if(id===requestId)document.querySelector('.workspace').setAttribute('aria-busy','false');}}
function setDate(day,followToday=false){state.date=day;state.followToday=followToday;if(day!=='undated')state.month=day.slice(0,7);state.page=1;state.query='';state.filter='all';$('search').value='';$('content-filter').value='all';loadData();}
function moveMonth(delta){if(!ready)return;const [y,m]=state.month.split('-').map(Number);state.month=new Date(Date.UTC(y,m-1+delta,1)).toISOString().slice(0,7);loadData();}
let searchTimer; $('search').addEventListener('input',e=>{state.query=e.target.value.trim();state.page=1;clearTimeout(searchTimer);searchTimer=setTimeout(loadData,200);});
$('content-filter').addEventListener('change',e=>{state.filter=e.target.value;state.page=1;loadData();});
$('sort').addEventListener('change',e=>{state.sort=e.target.value;state.page=1;loadData();});
$('timezone').addEventListener('change',e=>{state.zone=e.target.value;if(state.followToday){state.date=todayKey();state.month=state.date.slice(0,7);}state.page=1;loadData();});
$('previous').addEventListener('click',()=>{state.page--;loadData();});$('next').addEventListener('click',()=>{state.page++;loadData();});
$('month-previous').addEventListener('click',()=>moveMonth(-1));$('month-next').addEventListener('click',()=>moveMonth(1));
$('back-today').addEventListener('click',()=>setDate(todayKey(),true));$('undated').addEventListener('click',()=>setDate('undated'));
document.querySelector('.brand').addEventListener('click',e=>{e.preventDefault();state.source='all';state.query='';state.filter='all';state.sort='published';$('search').value='';$('content-filter').value='all';$('sort').value='published';$('content-filter').disabled=false;makeTabs();$('source-panel').setAttribute('aria-labelledby','tab-all');history.pushState(null,'','#news');showSection();setDate(todayKey(),true);window.scrollTo({top:0});});
function refresh(){if(activeSection==='malware')return;if(activeSection==='apt'){if(!document.querySelector('#apt-grid details[open]'))loadApt();return;}if(!ready)return boot();if(state.followToday&&state.date!==todayKey()){state.date=todayKey();state.month=state.date.slice(0,7);state.page=1;}loadData();}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
let refreshTimer;async function boot(){try{const [config,sourceResult]=await Promise.all([getJson('/config.json'),getJson('/api/v1/sources')]);DATA.sources=sourceResult.items;sources=new Map(DATA.sources.map(s=>[s.id,s]));state.zone=config.display_timezone;if(!Array.from($('timezone').options).some(x=>x.value===state.zone)){const opt=el('option','',state.zone);opt.value=state.zone;$('timezone').append(opt);}$('timezone').value=state.zone;state.date=todayKey();state.month=state.date.slice(0,7);ready=true;makeTabs();await loadData();clearInterval(refreshTimer);refreshTimer=setInterval(refresh,config.refresh_seconds*1000);}catch(error){$('api-message').hidden=false;$('api-message').className='api-message error';$('api-message').textContent='无法连接新闻服务。请先启动收集服务，然后刷新页面重试。';}}

let activeSection='news',aptRequest=0,aptController=null,aptSearchTimer;
const aptState={page:1,days:30,q:'',activity:'all',sort:'activity',zone:'Europe/Brussels'};
function showSection(){
  activeSection=['news','malware','apt'].includes(location.hash.slice(1))?location.hash.slice(1):'news';
  for(const section of ['news','malware','apt'])$(section+'-view').hidden=section!==activeSection;
  for(const a of document.querySelectorAll('.main-nav a')){
    if(a.dataset.section===activeSection)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
  }
  if(activeSection==='apt'){document.title='APT 组织档案 · AnquanDay';loadApt();}
  else if(activeSection==='malware'){document.title='恶意软件 · AnquanDay';aptController?.abort();}
  else{aptController?.abort();if(ready)renderHeading();else document.title='AnquanDay';}
}
window.addEventListener('hashchange',()=>{showSection();if(activeSection==='news')refresh();});

function aptStat(value,label){const node=el('div','apt-stat');node.append(el('strong','',fmt.format(value)),el('span','',label));return node;}
function aptPublished(p,zone){if(!p?.utc)return p?.date||p?.raw||'时间未确定';return new Intl.DateTimeFormat('sv-SE',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(p.utc));}
function aptCard(group,windowInfo){
  const a=group.activity,card=el('article','apt-card'),top=el('div','apt-card-top'),heading=el('div');
  heading.append(el('h2','',group.name),el('span','apt-id',group.id));
  const badge=el('span','activity-badge '+a.level,a.label);badge.title='报道活跃度，不代表实际攻击频率';top.append(heading,badge);
  const stats=el('div','apt-card-stats');
  for(const [value,label] of [[a.headline_articles,'标题提及'],[a.body_only_articles,'正文提及'],[a.sources,'个来源']]){const item=el('span');item.append(el('b','',value),document.createTextNode(label));stats.append(item);}
  const last=a.latest_published?'最近报道 '+aptPublished(a.latest_published,windowInfo.timezone):'当前窗口内未收录到相关报道';
  card.append(top,el('p','apt-intro',group.summary_zh||'中文简介待整理，请查看原始档案。'),stats,el('p','apt-last',last));
  const details=el('details'),summary=el('summary','',a.articles?'查看相关报道 · '+a.articles+' 篇':'相关报道 · 暂无收录'),reports=el('div','apt-evidence');
  details.append(summary,reports);let opened=false;
  details.addEventListener('toggle',()=>{if(details.open&&!opened){opened=true;loadAptReports(group.id,windowInfo,reports,1);}});card.append(details);
  const profile=el('details'),body=el('div','apt-profile');profile.append(el('summary','','组织介绍与原始资料'));
  body.append(el('p','',group.summary_zh||'中文简介待整理。'));
  if(group.associated_names.length)body.append(el('p','apt-aliases','关联名称：'+group.associated_names.join(' · ')));
  body.append(link('查看 MITRE 原始档案 ↗',group.source_url),el('p','','资料版本 '+group.source_version+' · 原档案更新 '+group.modified.slice(0,10)));
  const original=el('details');original.append(el('summary','','展开英文原始简介'),el('div','apt-original',group.description_en));body.append(original);
  if(group.translation)body.append(el('p','','中文整理：'+group.translation.model+' · '+group.translation.translated_at.slice(0,10)+' · 以原始资料为依据'));
  profile.append(body);card.append(profile);return card;
}
async function loadAptReports(group,windowInfo,target,page){
  target.replaceChildren(el('p','','正在读取相关报道…'));
  try{
    const data=await getJson('/api/v1/apt/articles',{group,date:windowInfo.end,days:windowInfo.days,tz:windowInfo.timezone,page,page_size:5});
    target.replaceChildren();
    if(!data.total)target.append(el('p','','所选时间窗口内暂无收录。这不表示该组织停止活动。'));
    for(const report of data.items){
      const row=el('div','apt-report');row.append(link(displayTitle(report),report.url));
      row.append(el('div','apt-report-meta',report.source_name+' · '+aptPublished(report.published,windowInfo.timezone)+' · '+(report.match_kind==='title'?'标题提及':'仅正文 / 摘要提及')));
      row.append(el('div','apt-report-meta','匹配名称：'+[...new Set(report.matches.map(m=>m.name))].join('、')));
      const proof=el('details');proof.append(el('summary','','展开原文与匹配证据'));
      proof.append(el('p','',report.title));
      for(const match of report.matches){proof.append(el('div','apt-proof',(match.field==='title'?'标题':'正文 / 摘要')+' · '+match.name+'\n…'+match.excerpt+'…'));}
      proof.append(el('p','apt-report-meta','文章 #'+report.id+' · 内容版本 '+report.revision_hash.slice(0,12)+' · 片段已统一大小写，原始表述请核对原文。'));
      row.append(proof);target.append(row);
    }
    if(data.pages>1){const actions=el('div','page-actions'),prev=el('button','','← 上一页'),next=el('button','','下一页 →');prev.type=next.type='button';prev.disabled=data.page===1;next.disabled=data.page===data.pages;prev.onclick=()=>loadAptReports(group,windowInfo,target,data.page-1);next.onclick=()=>loadAptReports(group,windowInfo,target,data.page+1);actions.append(prev,el('span','',data.page+' / '+data.pages),next);target.append(actions);}
  }catch{target.replaceChildren(el('p','','相关报道暂时读取失败。'));const retry=el('button','detail-btn','重试');retry.type='button';retry.onclick=()=>loadAptReports(group,windowInfo,target,page);target.append(retry);}
}
async function loadApt(){
  const id=++aptRequest;aptController?.abort();aptController=new AbortController();
  $('apt-message').hidden=false;$('apt-message').className='api-message';$('apt-message').textContent='正在读取组织档案和报道统计…';$('apt-grid').setAttribute('aria-busy','true');
  $('apt-previous').disabled=$('apt-next').disabled=true;
  try{
    const data=await getJson('/api/v1/apt/groups',{days:aptState.days,q:aptState.q,activity:aptState.activity,sort:aptState.sort,tz:aptState.zone,page:aptState.page,page_size:12},aptController.signal);
    if(id!==aptRequest)return;
    aptState.page=data.page;
    $('apt-overview').replaceChildren(aptStat(data.catalog.total,'公开组织档案'),aptStat(data.headline_groups,'组织有标题提及'),aptStat(data.coverage.articles_in_window,'篇新闻纳入观察窗口'));
    $('apt-catalog-stamp').textContent='MITRE ATT&CK · v'+data.catalog.versions.join(' / ');
    $('apt-scope').textContent=data.catalog.scope+' 目录获取日期：'+data.catalog.retrieved_at.slice(0,10)+'。';
    $('apt-result-count').textContent='共 '+data.total+' 个匹配组织 · 全部档案中 '+data.mentioned_groups+' 个有新闻提及';
    $('apt-window').textContent=data.window.start+' — '+data.window.end+' · '+$('apt-timezone').selectedOptions[0].textContent;
    if(data.coverage.first_report_day)$('apt-window').textContent+=' · 已收录新闻始于 '+data.coverage.first_report_day;
    $('apt-grid').replaceChildren(...data.items.map(g=>aptCard(g,data.window)));
    $('apt-empty').hidden=!!data.items.length;$('apt-message').hidden=true;
    $('apt-page-info').textContent=data.total?'第 '+((data.page-1)*data.page_size+1)+'–'+Math.min(data.page*data.page_size,data.total)+' 个 / 共 '+data.total+' 个':'共 0 个';
    $('apt-page-number').textContent=data.page+' / '+data.pages;$('apt-previous').disabled=data.page<=1;$('apt-next').disabled=data.page>=data.pages;
  }catch(error){
    if(id!==aptRequest||error.name==='AbortError')return;
    $('apt-grid').replaceChildren();$('apt-overview').replaceChildren();$('apt-empty').hidden=true;
    for(const key of ['apt-result-count','apt-window','apt-page-info','apt-page-number'])$(key).textContent='';
    $('apt-message').className='api-message error';$('apt-message').replaceChildren(document.createTextNode('组织档案暂时无法读取。 '));
    const retry=el('button','detail-btn','重试');retry.type='button';retry.onclick=loadApt;$('apt-message').append(retry);
  }finally{if(id===aptRequest)$('apt-grid').setAttribute('aria-busy','false');}
}
$('apt-search').addEventListener('input',e=>{aptState.q=e.target.value.trim();aptState.page=1;clearTimeout(aptSearchTimer);aptSearchTimer=setTimeout(loadApt,250);});
for(const [id,key] of [['apt-days','days'],['apt-activity','activity'],['apt-sort','sort'],['apt-timezone','zone']])$(id).addEventListener('change',e=>{aptState[key]=e.target.value;aptState.page=1;clearTimeout(aptSearchTimer);loadApt();});
$('apt-previous').addEventListener('click',()=>{aptState.page--;loadApt();$('apt-view').scrollIntoView({block:'start'});});
$('apt-next').addEventListener('click',()=>{aptState.page++;loadApt();$('apt-view').scrollIntoView({block:'start'});});
showSection();
boot();
