import {jobs,reports} from '../api/entities.js';
import {download} from '../api/client.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {status} from '../components/common/status.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
function deliveryRow(d){
 const ok=d.status==='success';
 const failed=d.status==='failed';
 const prints=ok?'<small>CSV SHA256 '+esc(d.checksum.slice(0,20))+'…'+(d.json_checksum?' · JSON '+esc(d.json_checksum.slice(0,20))+'…':'')+'</small>':'';
 const actions=ok
  ?'<button class="text-button" data-download="'+d.id+'" data-format="csv">CSV ↓</button><button class="text-button" data-download="'+d.id+'" data-format="json">JSON ↓</button><button class="text-button" data-verify="'+d.id+'">核验</button>'
  :failed?'<button class="text-button" data-retry="'+d.id+'" '+(session.user.role!=='admin'?'disabled':'')+'>重试归档</button>'
  :'<small>等待后台补完…</small>';
 return '<div class="delivery"><div><strong>'+esc(d.filename)+'</strong> '+status(d.status)
  +'<p>'+esc(d.destination)+' · 应归档 '+date(d.scheduled_at||d.created_at)+' · 记录于 '+date(d.created_at)+'</p>'
  +(failed?'<p class="delivery-error">失败原因：'+esc(d.error||'未知')+'</p>':'')
  +prints+'</div><div>'+actions+'</div></div>';
}
export async function reportPage(){
 const [list,deliveries,allJobs]=await Promise.all([reports.list(),reports.deliveries(),jobs.list()]);workspace.reports=list;
 const approved=allJobs.filter(j=>j.approved&&j.status==='completed');
 const failedCount=deliveries.filter(d=>d.status==='failed').length;
 return heading('05 / DISTRIBUTION','定时分发与归档','把批准后的结果交付到本地业务收件箱。每个到期时点只归档一次，CSV 与 JSON 对应同一次批准结果并留有校验指纹。','<button class="button" id="refresh-reports">刷新收件箱 ↻</button>')+'<div class="report-grid"><section class="panel"><div class="section-title"><h2>创建分发计划</h2></div><form id="report-form"><label>简报名称<input name="name" required minlength="2" value="日间业务复核简报"></label><label>已批准分析<select name="job_id" required>'+approved.map(j=>'<option value="'+j.id+'">'+esc(j.name)+'</option>').join('')+'</select></label><label>业务接收组<input name="destination" value="业务复核组 · 本地收件箱" required></label><div class="parameter-grid"><label>分发周期<select name="interval_seconds"><option value="86400">每日</option><option value="604800">每周</option><option value="60">每分钟（验收）</option></select></label><label>首次执行<select name="delay_seconds"><option value="0">立即</option><option value="60">1 分钟后</option><option value="3600">1 小时后</option></select></label></div><button type="submit" class="primary" '+(session.user.role!=='admin'||!approved.length?'disabled':'')+'>保存分发计划</button></form><p class="hint">由后台 worker 按到期时间生成文件。仅负责人可创建或暂停；接收组为本地标签。</p></section><section><div class="section-title"><h2>运行中的计划</h2><span>'+list.filter(r=>r.enabled).length+' 条已启用</span></div><div class="schedule-list">'+list.map(r=>'<article><div><strong>'+esc(r.name)+'</strong><p>'+esc(r.destination)+'</p><small>下次 '+date(r.next_run)+' · 周期 '+r.interval_seconds+' 秒</small></div><button class="button small" data-toggle="'+r.id+'" '+(session.user.role!=='admin'?'disabled':'')+'>'+(r.enabled?'暂停':'启用')+'</button></article>').join('')+'</div><section class="panel inbox"><div class="section-title"><h2>本地收件箱</h2><span>'+deliveries.length+' 份归档'+(failedCount?' · '+failedCount+' 份失败':'')+'</span></div>'+deliveries.map(deliveryRow).join('')+(!deliveries.length?'<p class="empty">计划执行后，文件会出现在此处。等待片刻后刷新。</p>':'')+'</section></section></div>';
}
function verifyMessage(v){
 if(v.ok)return '核验通过：CSV 与 JSON 指纹一致，且对应同一次批准结果';
 if(!v.csv.exists||!v.json.exists)return '核验未通过：归档文件缺失';
 if(v.csv.matches===false||v.json.matches===false)return '核验未通过：文件内容与记录指纹不一致';
 if(v.consistent===false)return '核验未通过：CSV 与 JSON 不对应同一次归档';
 return '核验未通过';
}
export function bindReports(refresh){document.querySelector('#refresh-reports').onclick=refresh;submit(document.querySelector('#report-form'),async f=>{await reports.create({name:f.get('name'),job_id:Number(f.get('job_id')),destination:f.get('destination'),interval_seconds:Number(f.get('interval_seconds')),delay_seconds:Number(f.get('delay_seconds'))});notify('计划已保存，后台将按到期时间归档');await refresh();});document.querySelectorAll('[data-toggle]').forEach(b=>b.onclick=async()=>{try{await reports.toggle(b.dataset.toggle);await refresh();}catch(e){notify(e.message,true);}});document.querySelectorAll('[data-download]').forEach(b=>b.onclick=()=>download(b.dataset.download,b.dataset.format).catch(e=>notify(e.message,true)));document.querySelectorAll('[data-retry]').forEach(b=>b.onclick=async()=>{try{await reports.retry(b.dataset.retry);notify('归档已重新生成');}catch(e){notify(e.message,true);}await refresh();});document.querySelectorAll('[data-verify]').forEach(b=>b.onclick=async()=>{try{const v=await reports.verify(b.dataset.verify);notify(verifyMessage(v),!v.ok);}catch(e){notify(e.message,true);}});}
