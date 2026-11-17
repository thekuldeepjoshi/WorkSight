import React, { useEffect, useRef, useState } from 'react';

export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

export default function Workspace({ onLoad, onClear }) {
  const [mode, setMode] = useState('process');
  const [videoFile, setVideoFile] = useState(null);
  const [csvFile, setCsvFile] = useState(null);
  const [video, setVideo] = useState(null);
  const [zones, setZones] = useState({ conveyor: [], work_area: [] });
  const [zone, setZone] = useState('conveyor');
  const [fps, setFps] = useState(5);
  const [second, setSecond] = useState(0);
  const [job, setJob] = useState(null);
  const [saved, setSaved] = useState([]);
  const [imports, setImports] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const svg = useRef(null);
  const drag = useRef(null);

  useEffect(() => { api('/videos').then(setSaved).catch(e => setError(e.message)); }, []);
  useEffect(() => {
    api('/imports').then(setImports).catch(e => setError(e.message));
    const pending = localStorage.getItem('worksight-pending-run');
    if (pending) api(`/runs/${pending}`).then(status => {
      setJob(status);
      if (status.status === 'complete') api(`/analyses/${pending}`).then(onLoad).catch(e => setError(e.message));
    }).catch(() => localStorage.removeItem('worksight-pending-run'));
  }, []);
  useEffect(() => {
    if (!job || !['queued', 'processing'].includes(job.status)) return;
    let disposed = false;
    const timer = setInterval(async () => {
      try {
        const status = await api(`/runs/${job.id}`);
        if (disposed) return;
        if (status.status === 'complete') {
          localStorage.removeItem('worksight-pending-run');
          const analysis = await api(`/analyses/${job.id}`);
          if (!disposed) { setJob(status); onLoad(analysis); }
        } else setJob(status);
        if (status.status === 'failed') localStorage.removeItem('worksight-pending-run');
      } catch (e) { if (!disposed) setError(e.message); }
    }, 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, [job?.id, job?.status]);

  async function perform(action) {
    setBusy(true); setError('');
    try { await action(); } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  function point(e) {
    const local = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.current.getScreenCTM().inverse());
    return [Math.max(0, Math.min(1, local.x / video.width)), Math.max(0, Math.min(1, local.y / video.height))];
  }
  const processing = job && ['queued', 'processing'].includes(job.status);
  return <section className="panel workspace-panel">
    <div className="workspace-actions"><button className={`button ${mode === 'process' ? 'primary' : 'secondary'}`} onClick={() => setMode('process')}>Process new video</button><button className={`button ${mode === 'import' ? 'primary' : 'secondary'}`} onClick={() => setMode('import')}>Load Analysis</button><button className="small-button" onClick={() => { onClear(); setVideo(null); setVideoFile(null); setCsvFile(null); setError(''); }}>Clear Analysis</button></div>
    {mode === 'import' ? <div className="workspace-form"><h2>Load a video and events CSV</h2><p>Select the matching files. A valid CSV with zero events is supported.</p><label>Video <input aria-label="Analysis video" type="file" accept=".mp4,.mov,.webm" onChange={e => setVideoFile(e.target.files[0])}/></label><label>Events CSV <input aria-label="Events CSV" type="file" accept=".csv" onChange={e => setCsvFile(e.target.files[0])}/></label><button className="button primary" disabled={busy || !videoFile || !csvFile} onClick={() => perform(async () => { const body = new FormData(); body.append('video', videoFile); body.append('csv', csvFile); onLoad(await api('/imports', { method: 'POST', body })); })}>{busy ? 'Loading…' : 'Load Analysis'}</button></div>
      : <div className="workspace-form"><h2>Process an unloading video</h2><label>Original video <input type="file" accept=".mp4,.mov,.avi,.webm" onChange={e => { setVideoFile(e.target.files[0]); setVideo(null); }}/></label><button className="button secondary" disabled={!videoFile || busy || processing} onClick={() => perform(async () => { const body = new FormData(); body.append('video', videoFile); const result = await api('/videos', { method: 'POST', body }); setVideo(result); setZones({ conveyor: [], work_area: [] }); setSecond(0); })}>{busy ? 'Uploading…' : 'Upload and draw zones'}</button>
      {video && <><label>Preview time (seconds)<input type="number" min="0" max={Math.max(0, video.duration - 1)} value={second} onChange={e => setSecond(Math.max(0, Number(e.target.value)))}/></label><div className="workspace-actions"><button className={`button ${zone === 'conveyor' ? 'primary' : 'secondary'}`} onClick={() => setZone('conveyor')}>Conveyor · red</button><button className={`button ${zone === 'work_area' ? 'primary' : 'secondary'}`} onClick={() => setZone('work_area')}>Working area · blue</button><button className="small-button" onClick={() => setZones({ ...zones, [zone]: zones[zone].slice(0, -1) })}>Undo point</button><button className="small-button" onClick={() => setZones({ ...zones, [zone]: [] })}>Clear zone</button></div><p>Click to add small points. Drag existing points to adjust. At least three points per polygon.</p>
      <svg className="zone-editor" ref={svg} viewBox={`0 0 ${video.width} ${video.height}`} role="img" aria-label="Draw and edit operational zones" onPointerDown={e => { if (e.target.tagName === 'circle') return; const p = point(e); setZones(current => ({ ...current, [zone]: [...current[zone], p] })); }} onPointerMove={e => { if (!drag.current) return; const p = point(e); const { name, index } = drag.current; setZones(current => ({ ...current, [name]: current[name].map((v, i) => i === index ? p : v) })); }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        <image href={`/api/videos/${video.id}/frame?seconds=${second}`} width={video.width} height={video.height}/>
        {Object.entries(zones).map(([name, points]) => <g key={name}><polygon points={points.map(([x, y]) => `${x * video.width},${y * video.height}`).join(' ')} fill={name === 'conveyor' ? '#ff000025' : '#0088ff25'} stroke={name === 'conveyor' ? '#ff4444' : '#29a3ff'} strokeWidth="2" pointerEvents="none"/>{points.map(([x, y], index) => <circle key={index} cx={x * video.width} cy={y * video.height} r={Math.max(3, video.width / 250)} fill={name === 'conveyor' ? '#ff4444' : '#29a3ff'} onPointerDown={e => { e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { name, index }; }}/>)}</g>)}
      </svg><label>Analysis FPS <select value={fps} onChange={e => setFps(Number(e.target.value))}>{[5, 10, 15, 30].map(v => <option key={v}>{v}</option>)}</select></label><p>Pose, MediaPipe Hands and package detection are always enabled. Lower FPS processes faster.</p><button className="button primary" disabled={busy || processing || Object.values(zones).some(p => p.length < 3)} onClick={() => perform(async () => { await api(`/videos/${video.id}/zones`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(zones) }); const result = await api(`/videos/${video.id}/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ analysis_fps: fps }) }); localStorage.setItem('worksight-pending-run', result.id); setJob({ ...result, status: 'queued', progress: 0 }); })}>Process Video</button></>}
      {job && <div className="job-status" role="status"><b>Processing: {job.status}</b><progress max="1" value={job.progress || 0}/><span>{Math.round((job.progress || 0) * 100)}%</span>{['processing', 'paused'].includes(job.status) && <div className="workspace-actions"><button className="small-button" onClick={() => perform(async () => { const next = await api(`/runs/${job.id}/${job.status === 'paused' ? 'resume' : 'pause'}`, { method: 'POST' }); setJob(next); })}>{job.status === 'paused' ? 'Resume processing' : 'Pause processing'}</button><button className="small-button danger-button" onClick={() => perform(async () => { const next = await api(`/runs/${job.id}/cancel`, { method: 'POST' }); localStorage.removeItem('worksight-pending-run'); setJob(next); })}>Cancel processing</button></div>}{job.error && <p role="alert">{job.error}</p>}</div>}</div>}
    {error && <p className="workspace-error" role="alert">{error}</p>}
    <div className="workspace-form"><h3>Saved analyses</h3>{imports.map(item => <button className="small-button" key={item.id} onClick={() => perform(async () => onLoad(await api(`/imports/${item.id}`)))}>{item.name} · imported analysis</button>)}{saved.filter(v => v.status === 'complete').map(v => <button className="small-button" key={v.id} onClick={() => perform(async () => onLoad(await api(`/analyses/${v.id}`)))}>{v.source_name} · {v.processed_at}</button>)}{saved.every(v => v.status !== 'complete') && <p>No completed analyses yet. Process a video or load an existing pair.</p>}</div>
  </section>;
}
