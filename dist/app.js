var app={data:{settings:{wallet:"",autoSync:true},pools:[],lastSync:null,lastError:null},backend:true,filter:"active",chart:{}};
var $=function(s,r){return(r||document).querySelector(s)};
var $$=function(s,r){return Array.from((r||document).querySelectorAll(s))};
var money=new Intl.NumberFormat("pt-BR",{style:"currency",currency:"USD",maximumFractionDigits:2});
var num=new Intl.NumberFormat("pt-BR",{maximumFractionDigits:2});
function esc(v){return String(v==null?"":v).replace(/[&<>"']/g,function(c){return({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]})}
function localIso(v){var d=v?new Date(v):new Date(),z=new Date(d.getTime()-d.getTimezoneOffset()*60000);return z.toISOString().slice(0,16)}
function when(v){return v?new Intl.DateTimeFormat("pt-BR",{dateStyle:"short",timeStyle:"short"}).format(new Date(v)):"—"}
function toast(msg,error){var t=$("#toast");t.textContent=msg;t.className="toast show"+(error?" error":"");clearTimeout(toast.timer);toast.timer=setTimeout(function(){t.className="toast"},3500)}
async function api(path,options){var r=await fetch(path,Object.assign({headers:{"Content-Type":"application/json"}},options||{})),b=await r.json().catch(function(){return{}});if(!r.ok)throw new Error(b.error||"Não foi possível concluir.");return b}
function localState(){try{return JSON.parse(localStorage.getItem("neutralis-pools")||"null")}catch(e){return null}}
function saveLocal(){localStorage.setItem("neutralis-pools",JSON.stringify(app.data))}
function latest(p){return p.live||(p.snapshots&&p.snapshots.length?p.snapshots[p.snapshots.length-1]:{value:p.initialValue||0,fees:0,price:null,capturedAt:p.createdAt})}
function days(p,at){return Math.max(1,(new Date(at||Date.now())-new Date(p.startedAt||p.createdAt||Date.now()))/86400000)}
function poolAge(p){var opened=new Date(p.startedAt||p.createdAt||Date.now()),count=Math.max(0,Math.floor((Date.now()-opened)/86400000));return count+" "+(count===1?"dia":"dias")+" de pool · aberta em "+when(opened)}
function metrics(p){var l=latest(p),rows=p.snapshots||[],i=+p.initialValue||0,c=+l.value||0,raw=+l.fees||0,baseline=p.feesBaseline!=null?+p.feesBaseline:0,f=Math.max(0,raw-baseline),v=c-i,pnl=l.pnl!=null?+l.pnl:v+f,elapsed=days(p,l.capturedAt||l.date),apr=l.apr!=null?+l.apr:(p.source==="byreal"&&p.historyScope==="byreal-lifetime"&&i&&elapsed>=.9?f/i*365/elapsed*100:rows.length>1&&elapsed>=.9&&i?f/i*365/elapsed*100:null);return{initial:i,current:c,fees:f,variation:v,pnl:pnl,apr:apr,price:l.price}}
function tone(v){return v>0?"positive":v<0?"negative":""}
function signed(v){return(v>=0?"+":"")+money.format(v)}
function historyValues(p,key){var rows=p.snapshots||[],feeBase=p.feesBaseline!=null?+p.feesBaseline:0;return rows.map(function(s){var tracked=Math.max(0,(+s.fees||0)-feeBase);if(key==="fees")return tracked;if(key==="apr"){if(s.apr!=null)return+s.apr;return p.initialValue&&days(p,s.capturedAt||s.date)>=.9?tracked/p.initialValue*365/days(p,s.capturedAt||s.date)*100:0}return+s.value||0})}
function chartStats(p,key){var rows=p.snapshots||[],vals=historyValues(p,key),items=[];
  if(key==="dailyFees"){var daily=rows.slice(1).map(function(s,i){return Math.max(0,(+s.fees||0)-(+rows[i].fees||0))});if(!daily.length)return'<div class="chartValues"><span>Aguardando a primeira janela completa de 24 horas.</span></div>';var total=daily.reduce(function(a,v){return a+v},0);items=[["Últimas 24h",money.format(daily[daily.length-1])],["Melhor período",money.format(Math.max.apply(null,daily))],["Média",money.format(total/daily.length)]]}
  else if(key==="value"){var start=vals.length?vals[0]:+p.initialValue||0,current=vals.length?vals[vals.length-1]:start;items=[["Primeiro registro",money.format(start)],["Atual",money.format(current)],["Variação",signed(current-start)]]}
  else if(key==="fees"){var first=vals.length?vals[0]:0,last=vals.length?vals[vals.length-1]:0;items=[["Primeiro registro",money.format(first)],["Atual",money.format(last)],["Crescimento",signed(last-first)]]}
  else{var firstApr=vals.length?vals[0]:0,lastApr=vals.length?vals[vals.length-1]:0,avg=vals.length?vals.reduce(function(a,v){return a+v},0)/vals.length:0;items=[["Primeiro registro",num.format(firstApr)+"%"],["Atual",num.format(lastApr)+"%"],["Média",num.format(avg)+"%"]]}
  return'<div class="chartValues">'+items.map(function(item){return'<span><small>'+esc(item[0])+'</small><b>'+esc(item[1])+'</b></span>'}).join("")+'</div>'
}
function chart(p,key){
  var rows=p.snapshots||[];if(rows.length<2)return'<div class="chartEmpty">A segunda coleta formará a primeira curva.</div>';
  if(key==="dailyFees"){
    var daily=rows.slice(1).map(function(s,i){return{value:Math.max(0,(+s.fees||0)-(+rows[i].fees||0)),at:s.capturedAt||s.date}}),top=Math.max.apply(null,daily.map(function(x){return x.value}).concat([1])),slot=284/daily.length,width=Math.max(3,Math.min(28,slot*.62));
    var bars=daily.map(function(item,i){var h=item.value/top*84,x=8+i*slot+(slot-width)/2,y=104-h;return'<rect x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" width="'+width.toFixed(1)+'" height="'+Math.max(2,h).toFixed(1)+'" rx="3" fill="#7b8cff"><title>'+esc(when(item.at))+': '+esc(money.format(item.value))+'</title></rect>'}).join("");
    return'<svg viewBox="0 0 300 112" preserveAspectRatio="none" role="img" aria-label="Taxas coletadas em cada período de 24 horas"><path d="M8 104H292M8 60H292M8 16H292" stroke="#273650" stroke-width=".6"/>'+bars+'</svg>'
  }
  var vals=historyValues(p,key);
  var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);if(lo===hi){lo-=1;hi+=1}
  var pts=vals.map(function(v,i){return(8+i*284/Math.max(1,vals.length-1)).toFixed(1)+","+(104-(v-lo)/(hi-lo)*88).toFixed(1)}).join(" ");
  var color=key==="fees"?"#7b8cff":key==="apr"?"#ffbd66":"#55e7c6";
  return'<svg viewBox="0 0 300 112" preserveAspectRatio="none" role="img" aria-label="Evolução histórica"><path d="M8 104H292M8 60H292M8 16H292" stroke="#273650" stroke-width=".6"/><polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="2.5" vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round"/></svg>'
}
function card(p){
  var m=metrics(p),r=p.snapshots||[],first=r[0],last=r[r.length-1],mode=app.chart[p.id]||"value",label=mode==="value"?"Liquidez":mode==="fees"?"Taxas acumuladas":mode==="dailyFees"?"Taxas coletadas em 24h":"APR de taxas",feeLabel=p.source==="byreal"?"Taxas totais":"Taxas desde cadastro",pnlLabel=p.source==="byreal"?"PnL Byreal":"PnL com taxas",aprLabel=p.source==="byreal"?"APR desde abertura":"APR observado";
  var inside=m.price==null||p.rangeMin==null||p.rangeMax==null?null:m.price>=p.rangeMin&&m.price<=p.rangeMax;
  return'<article class="pool" data-id="'+esc(p.id)+'"><div class="poolTop"><div class="pair"><div class="coin">'+esc((p.token0||"?").slice(0,2))+'</div><div><h3>'+esc(p.name)+'</h3><p>'+esc(p.exchange)+' · '+esc(p.network)+' · '+(p.source==="byreal"?"Automática":"Manual")+'</p><p class="poolAge">'+esc(poolAge(p))+'</p></div></div><span class="badge '+(p.status==="closed"?"closed":inside===false?"out":"")+'">'+(p.status==="closed"?"Fechada":inside===false?"Fora da faixa":"Ativa")+'</span></div>'+
  '<div class="poolStats"><div class="poolStat"><span>Liquidez inicial</span><strong>'+money.format(m.initial)+'</strong></div><div class="poolStat"><span>Liquidez atual</span><strong>'+money.format(m.current)+'</strong></div><div class="poolStat"><span>'+feeLabel+'</span><strong class="positive">'+money.format(m.fees)+'</strong></div><div class="poolStat"><span title="Na Byreal, usa o PnL informado pela posição">'+pnlLabel+'</span><strong class="'+tone(m.pnl)+'">'+signed(m.pnl)+'</strong></div><div class="poolStat"><span title="Taxas acumuladas anualizadas pelo tempo desde a abertura">'+aprLabel+'</span><strong>'+(m.apr==null?"—":num.format(m.apr)+"%")+'</strong></div></div>'+
  '<div class="range"><span>Faixa <b>'+(p.rangeMin==null?"—":num.format(p.rangeMin))+' – '+(p.rangeMax==null?"—":num.format(p.rangeMax))+'</b></span><span>Preço <b>'+(m.price==null?"—":num.format(m.price))+'</b></span></div>'+
  '<div class="chartWrap"><div class="chartHead"><strong>Histórico · '+label+'</strong><div class="chartTabs"><button class="chartTab '+(mode==="value"?"active":"")+'" data-chart="value">Liquidez</button><button class="chartTab '+(mode==="dailyFees"?"active":"")+'" data-chart="dailyFees">Taxas 24h</button><button class="chartTab '+(mode==="fees"?"active":"")+'" data-chart="fees">Acumulado</button><button class="chartTab '+(mode==="apr"?"active":"")+'" data-chart="apr">APR</button></div></div><div class="chart">'+chart(p,mode)+'</div>'+chartStats(p,mode)+'<div class="chartNote"><span>'+(first?when(first.capturedAt||first.date):"—")+'</span><span>'+r.length+' registros</span><span>'+(last?when(last.capturedAt||last.date):"—")+'</span></div></div>'+
  '<div class="poolFoot"><div class="next">'+(p.status==="closed"?"Histórico encerrado em "+when(p.closedAt):p.nextSnapshotAt?"Próxima coleta: "+when(p.nextSnapshotAt):"Atualização manual")+'</div><div class="poolActions"><button class="btn small" data-action="update">Atualizar</button>'+(p.status==="active"?'<button class="btn small" data-action="close">Fechar</button>':'')+'<button class="btn small danger" data-action="delete">Excluir</button></div></div></article>'
}
function summary(){
  var ms=app.data.pools.map(metrics),initial=ms.reduce(function(a,m){return a+m.initial},0),current=ms.reduce(function(a,m){return a+m.current},0),fees=ms.reduce(function(a,m){return a+m.fees},0),variation=current-initial,pnl=variation+fees,eligible=ms.filter(function(m){return m.apr!=null}),eligibleInitial=eligible.reduce(function(a,m){return a+m.initial},0),apr=eligibleInitial?eligible.reduce(function(a,m){return a+m.apr*m.initial},0)/eligibleInitial:null;
  $("#sumInitial").textContent=money.format(initial);$("#sumCurrent").textContent=money.format(current);$("#sumFees").textContent=money.format(fees);$("#sumVariation").textContent=signed(variation);$("#sumVariation").className=tone(variation);$("#sumVariationPct").textContent=(variation>=0?"+":"")+num.format(initial?variation/initial*100:0)+"%";$("#sumPnl").textContent=signed(pnl);$("#sumPnl").className=tone(pnl);$("#sumPnlPct").textContent=(pnl>=0?"+":"")+num.format(initial?pnl/initial*100:0)+"%";$("#sumApr").textContent=apr==null?"—":num.format(apr)+"%";$("#poolCount").textContent=app.data.pools.length+" pools";$("#activeCount").textContent=app.data.pools.filter(function(p){return p.status==="active"}).length+" ativas"
}
function render(){
  summary();var list=app.data.pools.filter(function(p){return app.filter==="all"||p.status===app.filter});
  $("#subtitle").textContent=list.length+" posições exibidas · histórico salvo no seu Umbrel";
  $("#pools").innerHTML=list.length?list.map(card).join(""):'<section class="empty"><i>⌁</i><h3>Nenhuma pool nesta lista</h3><p>Conecte a carteira para importar posições Byreal ou cadastre uma pool manual. O primeiro ponto é gravado na hora e o seguinte 24 horas depois.</p><button class="btn primary" data-empty>Conectar Byreal</button></section>';
  $("#syncMeta").textContent=(app.backend?"Monitor do Umbrel":"Modo local do navegador")+(app.data.lastSync?" · última "+when(app.data.lastSync):"")+(app.data.lastError?" · "+app.data.lastError:"");$("#wallet").value=app.data.settings&&app.data.settings.wallet||""
}
async function load(){try{app.data=await api("/api/state");app.backend=true}catch(e){app.backend=false;app.data=localState()||app.data}render()}
async function refresh(){app.data=await api("/api/state");render()}
function open(id){$(id).showModal()}
async function mutate(path,options,localFn){document.body.classList.add("loading");try{if(app.backend){await api(path,options);await refresh()}else{localFn();saveLocal();render()}toast("Alteração salva.")}catch(e){toast(e.message,true)}finally{document.body.classList.remove("loading")}}

$$("[data-close]").forEach(function(b){b.addEventListener("click",function(){b.closest("dialog").close()})});
$("#walletBtn").addEventListener("click",function(){open("#walletModal")});
$("#newBtn").addEventListener("click",function(){$("#startedAt").value=localIso();open("#poolModal")});
$$(".tab").forEach(function(b){b.addEventListener("click",function(){$$(".tab").forEach(function(x){x.classList.remove("active")});b.classList.add("active");app.filter=b.dataset.filter;render()})});

$("#pools").addEventListener("click",async function(e){
  if(e.target.closest("[data-empty]")){open("#walletModal");return}
  var box=e.target.closest(".pool");if(!box)return;
  var p=app.data.pools.find(function(x){return x.id===box.dataset.id});if(!p)return;
  var chartBtn=e.target.closest("[data-chart]");if(chartBtn){app.chart[p.id]=chartBtn.dataset.chart;render();return}
  var btn=e.target.closest("[data-action]");if(!btn)return;
  if(btn.dataset.action==="update"){
    var m=metrics(p),f=$("#updateForm");f.poolId.value=p.id;f.currentValue.value=m.current;f.fees.value=m.fees;f.currentPrice.value=m.price==null?"":m.price;f.capturedAt.value=localIso();$("#updateTitle").textContent="Atualizar "+p.name;open("#updateModal")
  }
  if(btn.dataset.action==="close"&&confirm("Fechar "+p.name+"? A coleta automática será interrompida e o histórico continuará salvo.")){
    await mutate("/api/pools/"+p.id+"/close",{method:"POST",body:"{}"},function(){p.status="closed";p.closedAt=new Date().toISOString();p.nextSnapshotAt=null})
  }
  if(btn.dataset.action==="delete"&&confirm("Excluir definitivamente "+p.name+" e todo o histórico?")){
    await mutate("/api/pools/"+p.id,{method:"DELETE"},function(){app.data.pools=app.data.pools.filter(function(x){return x.id!==p.id})})
  }
});

$("#poolForm").addEventListener("submit",async function(e){
  e.preventDefault();var form=e.currentTarget,f=new FormData(form),obj=Object.fromEntries(f.entries());obj.startedAt=new Date(obj.startedAt).toISOString();obj.currentValue=obj.initialValue;obj.fees=0;
  await mutate("/api/pools",{method:"POST",body:JSON.stringify(obj)},function(){
    var now=new Date().toISOString();app.data.pools.push(Object.assign(obj,{id:crypto.randomUUID(),source:"manual",status:"active",initialValue:+obj.initialValue,createdAt:now,updatedAt:now,nextSnapshotAt:null,snapshots:[{date:now.slice(0,10),capturedAt:now,value:+obj.initialValue,fees:0,source:"manual"}]}))
  });
  form.closest("dialog").close();form.reset()
});

$("#updateForm").addEventListener("submit",async function(e){
  e.preventDefault();var form=e.currentTarget,f=new FormData(form),obj=Object.fromEntries(f.entries()),id=obj.poolId;delete obj.poolId;obj.capturedAt=new Date(obj.capturedAt).toISOString();
  await mutate("/api/pools/"+id+"/snapshots",{method:"POST",body:JSON.stringify(obj)},function(){
    var p=app.data.pools.find(function(x){return x.id===id});p.snapshots.push({date:obj.capturedAt.slice(0,10),capturedAt:obj.capturedAt,value:+obj.currentValue,fees:+obj.fees,price:obj.currentPrice?+obj.currentPrice:null,source:"manual"})
  });
  form.closest("dialog").close()
});

$("#walletForm").addEventListener("submit",async function(e){
  e.preventDefault();var form=e.currentTarget;if(!app.backend){toast("A sincronização Byreal funciona na instalação do Umbrel.",true);return}
  var wallet=$("#wallet").value.trim();document.body.classList.add("loading");
  try{
    await api("/api/settings",{method:"PUT",body:JSON.stringify({wallet:wallet,autoSync:true})});
    var result=await api("/api/byreal/sync",{method:"POST",body:JSON.stringify({wallet:wallet})});
    await refresh();form.closest("dialog").close();toast(result.found+" posição(ões) Byreal sincronizada(s).")
  }catch(err){toast(err.message,true)}finally{document.body.classList.remove("loading")}
});

$("#exportBtn").addEventListener("click",function(){
  var blob=new Blob([JSON.stringify(app.data,null,2)],{type:"application/json"}),a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="neutralis-pools-"+new Date().toISOString().slice(0,10)+".json";a.click();URL.revokeObjectURL(a.href)
});

if(document.modelContext&&document.modelContext.registerTool){
  var webTools=new AbortController();
  Promise.resolve(document.modelContext.registerTool({
    name:"get_pool_summary",title:"Consultar resumo das pools",
    description:"Retorna liquidez, taxas, PnL, APR e próximo horário de coleta das pools cadastradas.",
    inputSchema:{type:"object",properties:{status:{type:"string",enum:["active","closed","all"]}},additionalProperties:false},
    annotations:{readOnlyHint:true,untrustedContentHint:false},
    execute:function(input){
      if(input==null)input={};
      if(typeof input!=="object"||Array.isArray(input)||Object.keys(input).some(function(k){return k!=="status"})||(input.status&&["active","closed","all"].indexOf(input.status)<0))throw new Error("status deve ser active, closed ou all");
      var status=input&&input.status||"all",items=app.data.pools.filter(function(p){return status==="all"||p.status===status}).map(function(p){var m=metrics(p);return{id:p.id,name:p.name,status:p.status,liquidity:m.current,fees:m.fees,pnl:m.pnl,apr:m.apr,nextSnapshotAt:p.nextSnapshotAt||null}});
      return{count:items.length,pools:items,lastSync:app.data.lastSync||null}
    }
  },{signal:webTools.signal})).catch(function(){});
}
load();
