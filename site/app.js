'use strict';
const $=id=>document.getElementById(id);
function read(key,fallback){try{return JSON.parse(localStorage.getItem(key))??fallback}catch{return fallback}}
function store(key,value){try{localStorage.setItem(key,JSON.stringify(value))}catch{$('status').textContent='Browser storage is unavailable. Saves and preferences will last only this visit.'}}
let prefs=read('scout-preferences',{initial:true,basis:'discovered'}),saved=read('scout-saved',{}),view='today',data=null;
const previousVisit=read('scout-reviewed',null)||read('scout-last-visit',null);store('scout-last-visit',new Date().toISOString());
const fields=['function','location','search','season','basis','initial'];
function element(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e}
function date(value){return new Date(value).toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric',timeZone:'America/New_York'})}
function render(){
 if(!data)return;
 $('saved-count').textContent=Object.keys(saved).length;
 let list=view==='saved'?Object.values(saved).map(j=>data.jobs.find(x=>x.scout_job_id===j.scout_job_id)||{...j,source_current:false}):data.jobs;
 list=list.filter(j=>Scout.matches(j,prefs,view));
 $('count').textContent=`${list.length} opportunities`;
 $('result-title').textContent=view==='saved'?'Your saved opportunities':view==='today'?"Found today":'The last seven days';
 $('explanation').textContent=view==='saved'?'Saved on this device. Older saves may no longer be available.':view==='today'?'Jobs Scout found today, using Eastern Time. A job may have been posted earlier.':prefs.basis==='posted'?'Jobs the employer posted in the past 7 days. Jobs without a posting date are left out.':'Jobs Scout found in the past 7 days. Check each job for the employer’s posting date.';
 $('results').replaceChildren();
 if(!list.length){$('results').append(element('p',view==='saved'?'No saved jobs match. Save an opportunity or reset your filters.':'No opportunities match. Broaden your filters, include jobs from newly added companies, or check Last 7 days.','empty'));return}
 for(const j of list){const card=element('article','','card');card.append(element('p',j.company,'company'),element('h3',j.title),element('p',j.location.join(' · ')||'Location not specified','meta'));
 card.append(element('span',j.category==='OTHER'?'Other business':j.category.toLowerCase(),'badge'));
 if(j.season)card.append(element('span',j.season,'badge'));
 card.append(element('span',j.discovery_kind==='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'?'Newly found':j.discovery_kind==='INITIAL_DISCOVERY'?'Found when company was added':'Found earlier · date uncertain','badge'));
 if(previousVisit&&j.discovery_kind==='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'&&new Date(j.first_seen_at)>new Date(previousVisit))card.append(element('span','New since you reviewed','badge new'));
 if(!j.source_current)card.append(element('span','Availability needs checking — visit employer','badge stale'));
 const dates=element('div','','dates');dates.append(element('div',`Scout found: ${date(j.first_seen_at)}`),element('div',j.posted_at?`Employer posted: ${date(j.posted_at)}`:'Employer posting date unavailable'));card.append(dates);
 const actions=element('div','','actions'),apply=element('a','Apply ↗','apply');apply.href=j.canonical_apply_url;apply.target='_blank';apply.rel='noopener noreferrer';apply.setAttribute('aria-label',`Apply for ${j.title} at ${j.company}`);
 const save=element('button',saved[j.scout_job_id]?'Saved ✓':'Save','save');save.setAttribute('aria-pressed',String(!!saved[j.scout_job_id]));save.addEventListener('click',()=>{if(saved[j.scout_job_id])delete saved[j.scout_job_id];else saved[j.scout_job_id]=j;store('scout-saved',saved);render()});actions.append(apply,save);card.append(actions);$('results').append(card);
 }
}
for(const id of fields){$(id).addEventListener('input',()=>{prefs[id]=id==='initial'?$(id).checked:$(id).value;store('scout-preferences',prefs);render()})}
function restore(){for(const id of fields){if(id==='initial')$(id).checked=prefs.initial!==false;else $(id).value=prefs[id]|| (id==='basis'?'discovered':'')}}
$('reset').onclick=()=>{prefs={initial:true,basis:'discovered'};store('scout-preferences',prefs);restore();render()};
$('review').onclick=()=>{store('scout-reviewed',new Date().toISOString());$('review').textContent='Reviewed ✓'};
for(const b of document.querySelectorAll('nav button'))b.onclick=()=>{view=b.dataset.view;for(const x of document.querySelectorAll('nav button'))x.setAttribute('aria-pressed',String(x===b));render()};
fetch('data/feed.json',{cache:'no-cache'}).then(r=>{if(!r.ok)throw Error('Feed unavailable');return r.json()}).then(r=>{
 data=r;for(const s of [...new Set(r.jobs.map(j=>j.season).filter(Boolean))].sort())$('season').append(new Option(s,s));restore();
 const stale=Date.now()-new Date(r.last_successful_inventory)>864e5;
 $('status').textContent=`Updated ${new Date(r.generated_at).toLocaleString()} · ${r.priority_monitored} employers checked${stale?' · Updates are delayed — check availability with the employer':''}`;
 if(stale)$('status').classList.add('stale');render();
}).catch(()=>{$('status').textContent='Internships could not load. Try again later. Your saved opportunities are still available.';data={jobs:[]};restore();render()});
