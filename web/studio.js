const $=id=>document.getElementById(id);let sourceMode='youtube',lastJobs='',jobs=[],upload=null,uploadLimit=0,storageData=null,lastStorageAt=0,refreshing=false,pendingDeletion=null;
function node(tag,text,cls){const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e}
function source(mode){sourceMode=mode;$('youtubePanel').hidden=mode!=='youtube';$('uploadPanel').hidden=mode!=='upload';$('url').required=mode==='youtube';$('file').required=mode==='upload';$('youtubeTab').classList.toggle('selected',mode==='youtube');$('uploadTab').classList.toggle('selected',mode==='upload')}
$('youtubeTab').onclick=()=>source('youtube');$('uploadTab').onclick=()=>source('upload');$('file').onchange=()=>{upload=null;$('uploadStatus').textContent=$('file').files[0]?.name||''};
function updatePlan(){const count=Number($('max_clips').value),daily=Number($('daily').value);$('coverage').textContent=count>0&&daily>0?`Up to ${count} clips → ${Math.ceil(count/daily)} days of content.`:count===0?'All clips from the selected video range.':'Choose a clip count and daily pace.';localStorage.setItem('clipflow-daily',$('daily').value)}
$('daily').value=localStorage.getItem('clipflow-daily')||1;['daily','max_clips'].forEach(id=>$(id).oninput=updatePlan);updatePlan();
async function health(){try{let r=await fetch('/api/health');if(!r.ok)throw Error();let h=await r.json();uploadLimit=h.upload_limit??0;if(!h.ai){$('selection').value='even';$('selection').querySelector('[value="highlights"]').disabled=true;$('selectionNote').textContent='AI is unavailable on this server. Even Cuts is ready.';}if(h.public_access){$('workspaceNotice').hidden=false;$('workspaceNotice').textContent='Open to everyone — no sign-in needed. Your clips belong to this browser workspace. Keep cookies enabled and download results before closing or clearing browser data.';$('computeLocation').textContent='Your browser has its own workspace.';if($('browser')){$('browser').value='';$('browser').disabled=true;}if($('profile')){$('profile').value='';$('profile').disabled=true;}}if(h.cloud_lite){$('computeLocation').textContent=h.public_access?'Your browser has its own cloud workspace.':'Clips run on your cloud workspace.';$('uploadLimit').textContent=uploadLimit?'Up to '+Math.floor(uploadLimit/1024**2)+' MB':'No app size cap · available server storage applies';$('processingNote').textContent='All clips: no app-imposed duration or clip-count cap. 720p exports. Keep this tab open while processing; free-server storage and restarts can interrupt long jobs.';updatePlan()}$('engine').textContent=h.cloud_lite?'● Cloud engine · ready':'● Local engine · ready';}catch{$('engine').textContent='● Server unavailable'}}
function link(text,url,download=false){let a=node('a',text);a.href=url;if(download)a.download='';return a}
function render(){let visible=jobs.filter(j=>(j.title||'').toLowerCase().includes($('search').value.toLowerCase())&&($('filter').value==='all'||($('filter').value==='working'?!['ready','failed'].includes(j.status):j.status===$('filter').value)));$('jobsCount').textContent=jobs.length+' projects';$('jobs').replaceChildren();if(!visible.length){let empty=node('div',null,'card empty');empty.append(node('strong','✦'),node('h3',jobs.length?'No matching projects':'Your next story starts here'),node('p','Choose a recording and generate your first clips.'));$('jobs').append(empty);return}for(let j of visible){let card=node('section',null,'card job'),header=node('div',null,'job-header');header.append(node('h3',j.title),node('div',j.status+(j.total?` · ${j.clips.length}/${j.total} clips`:''),'status '+j.status));card.append(header);let settings=[(j.selection_used||j.selection)==='highlights'?'AI highlights':'Even cuts',j.layout||'original',j.detected_language?`Language: ${j.detected_language}`:''];card.append(node('div',settings.filter(Boolean).join(' · '),'job-detail'));const usage=storageData?.projects?.[j.id];let actions=node('div',null,'storage-actions');let removeOriginal=node('button',usage?.original_present?'Delete original video':j.original_deleted?'Original deleted':'Original unavailable','secondary'),removeProject=node('button','Delete project','danger');removeOriginal.type=removeProject.type='button';removeOriginal.disabled=!usage?.original_present||!usage.can_delete_original;removeProject.disabled=!['ready','failed'].includes(j.status);removeOriginal.title=usage&&!usage.can_delete_original?'Wait for all projects using this original to finish.':'Keeps generated clips and captions';removeProject.title=removeProject.disabled?'Wait for processing to finish.':'Deletes this project and its files';removeOriginal.onclick=()=>openDelete(j,'original',usage);removeProject.onclick=()=>openDelete(j,'project',usage);actions.append(removeOriginal,removeProject);card.append(actions);if(usage)card.append(node('p',`Project folder: ${bytes(usage.project_bytes)} · Original: ${bytes(usage.original_bytes)}${usage.original_shared?' · Shared by '+usage.shared_projects+' projects':''}`,'storage-details'));if(j.detail)card.append(node('p',j.detail,'job-detail'));if(j.total){let p=node('progress');p.max=j.total;p.value=j.clips.length;p.setAttribute('aria-label','Clips completed');card.append(p)}if(j.error)card.append(node('p',j.error,'job-error'));if(j.status==='failed'&&j.source_retry)card.append(sourceRetry(j));for(let warning of j.warnings||[])card.append(node('p',warning,'warning'));let links=node('div',null,'links');if(j.status==='ready')links.append(link('↓ Download all (.zip)','/api/bundle/'+j.id,true));if(j.transcript){links.append(link('↓ Transcript JSON','/media/'+j.id+'/transcript.json',true));let details=node('details'),summary=node('summary','Read transcript'),body=node('div','Open to load transcript','transcript');details.append(summary,body);details.ontoggle=async()=>{if(!details.open||details.dataset.loaded)return;try{let r=await fetch('/media/'+j.id+'/transcript.json');if(!r.ok)throw Error();let data=await r.json();body.textContent=data.segments.map(s=>`[${Math.floor(s.start/60)}:${String(Math.floor(s.start%60)).padStart(2,'0')}] ${s.text}`).join('\n\n');details.dataset.loaded='1'}catch{body.textContent='Could not load transcript.'}};card.append(details)}card.append(links);let clips=node('div',null,'clips');for(let c of j.clips){let item=node('article',null,'clip'),v=node('video');v.controls=true;v.preload='none';v.playsInline=true;v.src='/media/'+j.id+'/'+c.name;item.append(v,node('h4',c.title||c.name),node('p',`${c.start.toFixed(1)}s → ${(c.start+c.duration).toFixed(1)}s · ${c.duration.toFixed(1)} seconds`));if(c.relevance!=null)item.append(node('p',`Semantic relevance ${c.relevance.toFixed(3)} · ${c.reason}`));if(c.tracking&&c.tracking!=='off')item.append(node('p',c.face_samples?'Visual speaker framing applied':'No face found · centered frame'));let downloads=node('div',null,'links');downloads.append(link('↓ MP4',v.src,true));if(c.srt)downloads.append(link('↓ SRT','/media/'+j.id+'/'+c.srt,true));item.append(downloads);clips.append(item)}card.append(clips);$('jobs').append(card)}}
$('search').oninput=render;$('filter').onchange=render;
function bytes(value){if(!Number.isFinite(value))return '—';if(value===0)return '0 B';const units=['B','KB','MB','GB','TB'];const scale=Math.min(4,Math.floor(Math.log(value)/Math.log(1024)));return (value/1024**scale).toFixed(scale?1:0)+' '+units[scale]}
function showStorage(){if(!storageData)return;$('storageUsed').textContent=bytes(storageData.used_bytes);$('storageOriginal').textContent=bytes(storageData.original_bytes);$('storageClips').textContent=bytes(storageData.clip_bytes);$('storageFree').textContent=bytes(storageData.free_bytes);$('storageOther').textContent='Captions, metadata and other app files: '+bytes(storageData.other_bytes)+'. Shared originals are counted once in total usage.'}
async function refresh(forceStorage=false){if(refreshing)return;refreshing=true;try{let requests=[fetch('/api/jobs')];let loadStorage=forceStorage||!storageData||Date.now()-lastStorageAt>15000;if(loadStorage)requests.push(fetch('/api/storage'));const responses=await Promise.all(requests);if(!responses[0].ok)throw Error();let next=await responses[0].json();if(loadStorage&&responses[1].ok){storageData=await responses[1].json();lastStorageAt=Date.now();showStorage()}let signature=JSON.stringify(next)+JSON.stringify(storageData?.projects);if(signature!==lastJobs){jobs=next;lastJobs=signature;render()}}catch{$('engine').textContent='● Server unavailable'}finally{refreshing=false}}
function openDelete(job,action,usage){pendingDeletion={id:job.id,action,title:job.title};$('deleteTitle').textContent=action==='original'?'Delete original video?':'Delete entire project?';$('deleteDescription').textContent=action==='original'?`Remove the original video for “${job.title}” (${bytes(usage?.original_bytes)}). Generated clips, captions and transcript stay available.${usage?.original_shared?' This original is shared by '+usage.shared_projects+' projects; their clips also stay available.':''}`:`Remove “${job.title}”, all of its generated clips, captions and transcript.${usage?.original_shared?' Its shared original stays available for the other projects.':' Its original video will also be removed.'} Download anything you want to keep first.`;$('deleteError').textContent='';$('confirmDelete').textContent=action==='original'?'Delete original permanently':'Delete project permanently';$('deleteDialog').showModal()}
$('cancelDelete').onclick=()=>$('deleteDialog').close();$('deleteDialog').onclose=()=>{pendingDeletion=null};
$('confirmDelete').onclick=async()=>{if(!pendingDeletion)return;const selection={...pendingDeletion};$('confirmDelete').disabled=true;$('cancelDelete').disabled=true;$('deleteError').textContent='';try{const response=await fetch('/api/jobs/'+selection.id+'/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:selection.action,confirm:true})});const result=await response.json();if(!response.ok)throw Error(result.error||'Deletion failed');if(result.removed_upload&&upload?.upload===result.removed_upload){upload=null;$('uploadStatus').textContent='Original deleted. Choose the file again to create another project.'} $('deleteDialog').close();$('storageMessage').textContent=(selection.action==='original'?'Original deleted. Clips kept. ':'Project deleted. ')+bytes(result.freed_bytes)+' of files removed.';lastStorageAt=0;lastJobs='';await refresh(true)}catch(error){$('deleteError').textContent=error.message}finally{$('confirmDelete').disabled=false;$('cancelDelete').disabled=false}};
$('refreshStorage').onclick=()=>refresh(true);

function cleanCookies(raw){const text=String(raw||'').trim();if(!text)return '';const domains=['youtube','youtu.be','google','ytimg','ggpht'];let lines=[];if(text.startsWith('[')){try{for(const c of JSON.parse(text)){if(!c||!c.name)continue;const dom=String(c.domain||'.youtube.com').trim();if(domains.some(d=>dom.toLowerCase().includes(d)))lines.push(`${dom}\t${dom.startsWith('.')?'TRUE':'FALSE'}\t${c.path||'/'}\t${c.secure===false?'FALSE':'TRUE'}\t${Math.floor(Number(c.expirationDate||c.expires||0))}\t${c.name}\t${c.value||''}`)}}catch{}}if(!lines.length&&text.includes('\t')){let all=[];for(const r of text.split(/\r?\n/)){const l=r.trim();if(!l||(l.startsWith('#')&&!l.startsWith('#HttpOnly_')))continue;const p=l.split('\t');if(p.length>=7){const row=p.slice(0,7).join('\t'),dom=p[0].replace(/^#HttpOnly_/,'').toLowerCase();all.push(row);if(domains.some(d=>dom.includes(d)))lines.push(row)}}if(!lines.length)lines=all}return lines.length?'# Netscape HTTP Cookie File\n'+lines.join('\n'):text}
if($('cookies'))$('cookies').onchange=()=>{if($('cookies').value)$('cookies').value=cleanCookies($('cookies').value)};
function sourceRetry(job){
    const row=node('div',null,'storage-actions');
    const file=node('input');
    file.type='file';
    file.accept='.mp4,.mov,.mkv,.webm,.m4v';
    file.hidden=true;
    const button=node('button','Upload saved video & retry','secondary');
    button.type='button';
    const status=node('span',null,'note');
    status.setAttribute('role','status');
    button.onclick=()=>file.click();
    file.onchange=async()=>{
        const video=file.files[0];
        if(!video)return;
        button.disabled=true;
        status.textContent='Uploading…';
        try{
            const saved=await uploadVideo(video,status);
            const response=await fetch('/api/jobs/'+job.id+'/retry-upload',{
                method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(saved)
            });
            const result=await response.json();
            if(!response.ok)throw Error(result.error);
            await refresh(true);
        }catch(error){
            status.textContent=error.message;
        }finally{
            file.value='';
            button.disabled=false;
        }
    };
    row.append(file,button,status);
    return row;
}
function uploadVideo(file,status=$('uploadStatus')){return new Promise((resolve,reject)=>{let xhr=new XMLHttpRequest();xhr.open('POST','/api/upload?name='+encodeURIComponent(file.name));xhr.upload.onprogress=e=>{if(e.lengthComputable)status.textContent=`Uploading ${Math.round(e.loaded/e.total*100)}% · ${file.name}`};xhr.onload=()=>{try{if(xhr.status===413)throw Error('Hosting rejected this request size. The app has no upload cap, but the hosting platform may impose its own limit.');let data;try{data=JSON.parse(xhr.responseText)}catch{throw Error('Upload failed (HTTP '+xhr.status+'). Check the connection and available server storage.')} if(xhr.status!==201)throw Error(data.error);resolve(data)}catch(e){reject(e)}};xhr.onerror=()=>reject(Error('Upload failed. Check the local server.'));xhr.send(file)})}
$('form').onsubmit=async e=>{e.preventDefault();$('create').disabled=true;$('error').textContent='';try{if(sourceMode==='upload'&&!upload){const file=$('file').files[0];if(!file)throw Error('Choose a video first');if(uploadLimit&&file.size>uploadLimit)throw Error('Choose a video up to '+Math.floor(uploadLimit/1024**2)+' MB');$('create').textContent='Uploading…';upload=await uploadVideo(file);$('uploadStatus').textContent='✓ Video uploaded'}$('create').textContent='Creating project…';let body={url:sourceMode==='youtube'?$('url').value:'',rights:$('rights').checked,selection:$('selection').value};for(let id of ['minutes','max_clips','start','end','layout','browser','profile','cookies'])if($(id))body[id]=id==='cookies'?cleanCookies($(id).value):$(id).value;if(sourceMode==='upload')Object.assign(body,upload);let r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data=await r.json();if(!r.ok)throw Error(data.error);await refresh(true);$('jobs').scrollIntoView({behavior:'smooth',block:'start'})}catch(e){$('error').textContent=e.message}finally{$('create').disabled=false;$('create').textContent='✂  Generate clips'}};


const macCode=new URLSearchParams(location.hash.slice(1)).get('mac');
async function showMacConnection(){
    const notice=$('macConnection');
    try{
        const response=await fetch('/api/health');
        if(!response.ok)return;
        const state=await response.json();
        if(state.mac_paired){
            notice.hidden=false;
            notice.textContent=state.mac_online?'Mac connected · YouTube downloads run automatically on your Mac.':'Mac paired · waiting for the helper to connect.';
        }
    }catch{}
}
async function startStudio(){
if(macCode){
    history.replaceState(null,'',location.pathname+location.search);
    const notice=$('macConnection');
    notice.hidden=false;
    notice.textContent='Connecting your Mac…';
    await fetch('/api/mac/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:macCode})})
        .then(async response=>{
            const result=await response.json();
            if(!response.ok)throw Error(result.error);
            await showMacConnection();
        })
        .catch(error=>{notice.textContent=error.message;});
}
await health();
await refresh();
await showMacConnection();
setInterval(()=>refresh(),3000);
setInterval(showMacConnection,6000);
}
startStudio();
