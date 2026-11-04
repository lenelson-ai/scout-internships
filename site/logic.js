/* Pure filtering shared with the dependency-free test suite. */
(function(root){
function day(value){return new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value));}
function matches(j,p,view,now=new Date()){
 if(!p.initial&&j.discovery_kind==='INITIAL_DISCOVERY')return false;
 if(view!=='saved'){
  const date=view==='today'||p.basis!=='posted'?j.first_seen_at:j.posted_at;
  if(!date)return false;
  if(view==='today' ? day(date)!==day(now) : (new Date(date)<new Date(now-7*864e5)||new Date(date)>now))return false;
 }
 const title=j.title.toLowerCase(),text=(j.company+' '+j.title).toLowerCase();
 if(p.search&&!text.includes(p.search.toLowerCase()))return false;
 if(p.location&&!j.location.join(' ').toLowerCase().includes(p.location.toLowerCase()))return false;
 if(p.season&&j.season!==p.season)return false;
 const patterns={'Banking':/bank|capital markets/,'Investment Management':/investment|asset management|portfolio/,'Wealth Management':/wealth|private client/,'Business Operations':/operations|business|supply chain/};
 const cats={'Finance':'FINANCE','Corporate Strategy':'STRATEGY','Consulting':'CONSULTING','Other business internships':'OTHER'};
 return !p.function||(patterns[p.function]?patterns[p.function].test(title):j.category===cats[p.function]);
}
const api={matches,day};if(typeof module!=='undefined')module.exports=api;else root.Scout=api;
})(globalThis);
