import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, Bell, ChevronDown, CircleHelp, Download, Factory, FileUp,
  Gauge, Layers3, Play, ScanLine, Search, Settings2, ShieldCheck, Video,
} from 'lucide-react';
import './styles.css';
import './theme.css';
function parseCSV(text) {
  const rows = [];
  let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"' && quoted && text[i + 1] === '"') { cell += '"'; i += 1; }
    else if (char === '"') quoted = !quoted;
    else if (char === ',' && !quoted) { row.push(cell.trim()); cell = ''; }
    else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && text[i + 1] === '\n') i += 1;
      row.push(cell.trim());
      if (row.some(Boolean)) rows.push(row);
      row = []; cell = '';
    } else cell += char;
  }
  row.push(cell.trim());
  if (row.some(Boolean)) rows.push(row);
  if (quoted) return { error: 'CSV contains an unclosed quotation mark. Export it again from WorkSight.' };
  if (rows.length < 2) return { error: 'The CSV has no event rows. Export the reviewable events from WorkSight.' };
  const headers = rows.shift().map((h) => h.replace(/^\uFEFF/, '').toLowerCase());
  const at = (...names) => names.map((name) => headers.indexOf(name)).find((i) => i !== -1);
  const timeAt = at('time', 'timestamp', 'start time', 'start');
  const trackAt = at('track', 'track id');
  const signalAt = at('signal', 'event type', 'event');
  const durationAt = at('duration (s)', 'duration', 'duration seconds');
  const confidenceAt = at('confidence', 'score');
  const evidenceAt = at('evidence rule', 'evidence', 'rule');
  const interpretationAt = at('interpretation', 'why it matters');
  if ([timeAt, trackAt, signalAt, durationAt, confidenceAt].some((i) => i === undefined)) {
    return { error: 'CSV columns do not match WorkSight export. Required: Time, Track, Signal, Duration (s), Confidence.' };
  }
  const parsed = [];
  for (let i = 0; i < rows.length; i += 1) {
    const cells = rows[i];
    const stamp = cells[timeAt] || '';
    const bits = stamp.split(':').map(Number);
    if (![2, 3].includes(bits.length) || bits.some((n) => !Number.isInteger(n) || n < 0) || bits.at(-1) >= 60 || (bits.length === 3 && bits[1] >= 60)) {
      return { error: `Invalid event time on CSV row ${i + 2}: “${stamp}”. Use mm:ss or hh:mm:ss.` };
    }
    const seconds = bits.length === 3 ? bits[0] * 3600 + bits[1] * 60 + bits[2] : bits[0] * 60 + bits[1];
    const signal = (cells[signalAt] || '').toLowerCase().replaceAll('_', ' ').trim();
    if (!signal) return { error: `Missing signal on CSV row ${i + 2}.` };
    const duration = Number(cells[durationAt]);
    let confidence = Number((cells[confidenceAt] || '').replace('%', ''));
    if (cells[confidenceAt]?.includes('%')) confidence /= 100;
    else if (confidence > 1 && confidence <= 100) confidence /= 100;
    if (!Number.isFinite(duration) || duration <= 0 || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      return { error: `Invalid duration or confidence on CSV row ${i + 2}.` };
    }
    if (!cells[trackAt]) return { error: `Missing track ID on CSV row ${i + 2}.` };
    parsed.push({ id: i + 1, label: stamp, seconds, track: cells[trackAt], signal, duration, confidence, evidence: evidenceAt === undefined ? '' : (cells[evidenceAt] || ''), interpretation: interpretationAt === undefined ? '' : (cells[interpretationAt] || '') });
  }
  return parsed.length ? { events: parsed.sort((a, b) => a.seconds - b.seconds) } : { error: 'No valid events were found in this CSV.' };
}

const EVENT_PRESENTATION = {
  'candidate available no belt demand': {
    label: 'Idle / available candidate', type: 'worker',
    explanation: 'No package was detected on the belt or estimated near this worker. This suggests availability; check the clip before calling the worker idle.',
  },
  'candidate working with package': {
    label: 'Possible package handling', type: 'worker',
    explanation: 'Pose, proximity, or movement suggests package handling. It does not confirm that a package was picked up or unloaded.',
  },
  'candidate low activity': {
    label: 'Possible slowdown', type: 'worker',
    explanation: 'A person was outside the work area, carried a package within it, or showed low movement without handling cues. Review the clip before interpreting this as idle time or delay.',
  },
  'candidate waiting full belt': {
    label: 'Waiting candidate · belt has packages', type: 'operations',
    explanation: 'At least four package detections were on the belt without a hand or package-carry signal.',
  },
  'candidate belt bottleneck': {
    label: 'Possible belt bottleneck', type: 'operations',
    explanation: 'Packages were detected on the belt without a carry or approach signal.',
  },
  'candidate staffing pressure': {
    label: 'Possible staffing pressure', type: 'operations',
    explanation: 'Several package detections and candidate handling signals occurred together. Review sustained demand before making staffing changes.',
  },
  'candidate staffing mismatch': {
    label: 'Possible staffing mismatch', type: 'operations',
    explanation: 'At least three tracked people were in the work area while no more than one had a belt-activity cue in the same frame.',
  },
  'possible body on conveyor': {
    label: 'Possible person on conveyor', type: 'safety-high',
    explanation: 'Multiple lower-body pose points overlapped the conveyor zone. Treat as an urgent safety review cue and verify the video.',
  },
  'possible person in conveyor zone': {
    label: 'Person detected in conveyor zone', type: 'safety',
    explanation: 'An ankle or fallback footpoint entered the conveyor zone. This is a safety review cue, not a confirmed hazard.',
  },
  'possible throwing risk': {
    label: 'Possible throwing motion', type: 'safety',
    explanation: 'Rapid package movement near a wrist triggered this review cue. Confirm visually; it may be a tracking error.',
  },
  'possible hand belt proximity': {
    label: 'Possible hand-to-belt proximity', type: 'belt',
    explanation: 'Detected fingertips entered the conveyor zone. Proximity does not confirm contact.',
  },
  'possible belt interaction': {
    label: 'Possible belt interaction', type: 'belt',
    explanation: 'A pose-estimated wrist entered the conveyor zone. This does not confirm unloading.',
  },
  'work area presence': {
    label: 'Present in work area', type: 'worker',
    explanation: 'A tracked person was inside the configured work-area zone. Presence alone does not show whether they were working or idle.',
  },
};

function presentEvent(event) {
  return EVENT_PRESENTATION[event.signal] || {
    label: event.signal.replace(/\b\w/g, (letter) => letter.toUpperCase()),
    type: 'other',
    explanation: 'Review the video and supporting event details to interpret this signal.',
  };
}

const TOPIC_SIGNALS = {
  worker: new Set([
    'work area presence', 'candidate available no belt demand',
    'candidate working with package', 'candidate low activity', 'candidate waiting full belt',
    'possible belt interaction', 'possible hand belt proximity',
  ]),
  belt: new Set([
    'possible belt interaction', 'possible hand belt proximity',
  ]),
  resources: new Set([
    'candidate staffing pressure', 'candidate available no belt demand',
    'candidate low activity', 'candidate waiting full belt', 'candidate staffing mismatch',
  ]),
  bottlenecks: new Set([
    'candidate belt bottleneck', 'candidate waiting full belt', 'candidate low activity',
  ]),
  risks: new Set([
    'possible body on conveyor', 'possible person in conveyor zone',
    'possible throwing risk',
  ]),
};

function App() {
  const [screen, setScreen] = useState('upload');
  const [events, setEvents] = useState([]);
  const [source, setSource] = useState('');
  const [videoUrl, setVideoUrl] = useState('');
  const [videoSeconds, setVideoSeconds] = useState(0);
  const [csvName, setCsvName] = useState('');
  const [csvError, setCsvError] = useState('');
  const [videoError, setVideoError] = useState('');
  const [videoValid, setVideoValid] = useState(false);
  const [active, setActive] = useState(null);
  const [selectedTopic, setSelectedTopic] = useState('all');
  const [topicDetailsOpen, setTopicDetailsOpen] = useState(false);
  const [selectedSlowdownTrack, setSelectedSlowdownTrack] = useState(null);
  const [reviewSecond, setReviewSecond] = useState(0);
  const [notice, setNotice] = useState('Add your WorkSight events CSV and video to load the operations report.');
  const videoRef = useRef(null);
  const topicDetailsRef = useRef(null);
  const csvInput = useRef(null);
  const videoInput = useRef(null);
  const wasDataReady = useRef(false);
  const csvValid = events.length > 0 && !csvError;
  const latestEventTime = events.reduce((latest, event) => Math.max(latest, event.seconds + event.duration), 0);
  const timestampsMatch = !videoValid || latestEventTime <= videoSeconds + 1;
  const dataReady = csvValid && videoValid && timestampsMatch;

  useEffect(() => {
    const becameReady = dataReady && !wasDataReady.current;
    wasDataReady.current = dataReady;
    if (screen === 'upload' && becameReady) {
      setScreen('overview');
    }
  }, [dataReady, screen]);

  const activeEvent = events.find((event) => event.id === active) || null;
  const urgentSafetyEvents = events.filter((event) => event.signal === 'possible body on conveyor');
  const urgentSafetyEvent = activeEvent?.signal === 'possible body on conveyor'
    ? activeEvent
    : urgentSafetyEvents[0] || null;
  const topicEvents = Object.fromEntries(Object.entries(TOPIC_SIGNALS).map(([topic, signals]) => [
    topic,
    events.filter((event) => signals.has(event.signal)),
  ]));
  const distinctTracks = (rows) => new Set(rows.map((event) => event.track).filter((track) => track && track !== '—')).size;
  const activeWorkerTracks = distinctTracks(events.filter((event) => [
    'candidate working with package', 'possible belt interaction', 'possible hand belt proximity',
  ].includes(event.signal)));
  const availableWorkerTracks = distinctTracks(events.filter((event) => event.signal === 'candidate available no belt demand'));
  const waitingWorkerTracks = distinctTracks(events.filter((event) => event.signal === 'candidate waiting full belt'));
  const lowActivityEvents = events.filter((event) => event.signal === 'candidate low activity');
  const lowActivityWorkerTracks = distinctTracks(lowActivityEvents);
  const slowdownTracks = Object.values(lowActivityEvents.reduce((groups, event) => {
    const track = event.track;
    if (!groups[track]) groups[track] = { track, events: [], seconds: 0 };
    groups[track].events.push(event);
    groups[track].seconds += event.duration;
    return groups;
  }, {})).sort((a, b) => b.seconds - a.seconds || String(a.track).localeCompare(String(b.track), undefined, { numeric: true }));
  const activeSlowdownTrack = slowdownTracks.find((item) => item.track === selectedSlowdownTrack) || slowdownTracks[0] || null;
  const filteredEvents = selectedTopic === 'all' ? events : topicEvents[selectedTopic];
  const fmt = (n) => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, '0')}`;
  const activitySignals = new Set(['candidate working with package', 'possible belt interaction', 'possible hand belt proximity']);
  const minuteCount = Math.max(1, Math.ceil(Math.max(videoSeconds, latestEventTime) / 60));
  const minuteActivity = Array.from({ length: minuteCount }, (_, minute) => ({ minute, seconds: 0 }));
  events.filter((event) => activitySignals.has(event.signal)).forEach((event) => {
    const end = event.seconds + event.duration;
    for (let minute = Math.floor(event.seconds / 60); minute <= Math.floor(Math.max(event.seconds, end - 0.001) / 60); minute += 1) {
      const start = minute * 60;
      minuteActivity[minute].seconds += Math.max(0, Math.min(end, start + 60) - Math.max(event.seconds, start));
    }
  });
  const maxMinuteActivity = Math.max(1, ...minuteActivity.map((item) => item.seconds));
  const leastActivity = Math.min(...minuteActivity.map((item) => item.seconds));
  const mostActivity = Math.max(...minuteActivity.map((item) => item.seconds));
  const priorityRank = (event) => ({
    'possible body on conveyor': 0,
    'possible person in conveyor zone': 1,
    'candidate low activity': 2,
    'candidate belt bottleneck': 3,
    'possible belt interaction': 4,
  }[event.signal] ?? 5);
  const priorityEvents = [...events].sort((a, b) => (priorityRank(a) - priorityRank(b)) || (b.duration - a.duration)).slice(0, 8);
  const windowStart = Math.floor(reviewSecond / 30) * 30;
  const windowEnd = windowStart + 30;
  const windowEvents = events.filter((event) => event.seconds < windowEnd && event.seconds + event.duration >= windowStart);
  const windowTracks = [...new Set(windowEvents.map((event) => event.track).filter((track) => track && track !== '—' && track !== 'Team'))];
  const staffingMismatchEvents = events.filter((event) => event.signal === 'candidate staffing mismatch');
  const safetyEvents = events.filter((event) => ['possible body on conveyor', 'possible person in conveyor zone'].includes(event.signal));
  const selectedTopicEvents = selectedTopic === 'all' ? [] : topicEvents[selectedTopic];
  const selectedTopicSignals = [...new Set(selectedTopicEvents.map((event) => event.signal))];
  const timelineSeconds = Math.max(1, videoSeconds, latestEventTime);

  const seekTo = (seconds, label, eventId = null) => {
    setActive(eventId);
    setReviewSecond(seconds);
    const player = videoRef.current;
    if (!player || !videoValid) { setNotice('Video is not ready yet. Wait for both files to validate before opening an event.'); return; }
    const duration = Number.isFinite(player.duration) ? player.duration : videoSeconds;
    player.currentTime = Math.max(0, Math.min(seconds, duration));
    document.querySelector('#review')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    player.play().catch(() => setNotice(`Video positioned at ${label}. Press play to review the evidence.`));
  };
  const seek = (event) => seekTo(event.seconds, event.label, event.id);
  const showTopicDetails = (topic) => {
    setSelectedTopic(topic);
    setTopicDetailsOpen(true);
    window.setTimeout(() => topicDetailsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
  };
  const loadCSV = async (file) => {
    if (!file) return;
    setScreen('upload');
    setEvents([]); setCsvError('Validating CSV…'); setCsvName(file.name); setActive(null); setSelectedTopic('all'); setSelectedSlowdownTrack(null);
    const result = parseCSV(await file.text());
    if (result.error) { setCsvError(result.error); setNotice(result.error); return; }
    setEvents(result.events); setCsvError(''); setNotice(videoValid ? `Validated ${result.events.length} event rows in ${file.name}.` : `Validated ${result.events.length} event rows in ${file.name}. Now validate a video.`);
  };
  const loadVideo = (file) => {
    if (!file) return;
    setScreen('upload');
    setVideoValid(false); setVideoError('Checking video format and duration…'); setVideoSeconds(0); setActive(null); setSelectedTopic('all'); setSelectedSlowdownTrack(null);
    if (videoUrl?.startsWith('blob:')) URL.revokeObjectURL(videoUrl);
    setSource(file.name);
    if (!/\.(mp4|webm|mov)$/i.test(file.name) || file.size === 0) {
      setVideoUrl(''); setVideoError('Choose a non-empty MP4, WebM, or MOV video. AVI is not supported by browser playback.'); return;
    }
    if (!/annotated/i.test(file.name)) {
      setVideoUrl(''); setVideoError('This looks like the original video. Choose the annotated output from data/processed so the overlays are visible.'); return;
    }
    setVideoUrl(URL.createObjectURL(file)); setNotice(`Checking ${file.name}…`);
  };
  const markVideoReady = () => {
    const player = videoRef.current;
    if (!player || !Number.isFinite(player.duration) || player.duration <= 0) {
      setVideoError('The video has no readable duration. Choose another video export.'); return;
    }
    setVideoSeconds(player.duration); setVideoValid(true); setVideoError('');
    setNotice(`Validated ${source} (${fmt(player.duration)}).`);
  };
  const downloadReview = () => {
    const rows = [['Time', 'Track', 'Signal', 'Duration (s)', 'Confidence', 'Evidence rule', 'Interpretation'], ...events.map((e) => [e.label, e.track, e.signal, e.duration, e.confidence, e.evidence, e.interpretation])];
    const csv = rows.map((r) => r.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(',')).join('\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); link.download = 'worksight-review.csv'; link.click(); URL.revokeObjectURL(link.href);
  };

  return <div className={`app-shell ${screen === 'upload' ? 'upload-app-shell' : ''}`}>
    {screen !== 'upload' && <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><ScanLine size={19}/></div><span>worksight<span className="brand-dot">.</span></span></div>
      <div className="site-switch"><span className="site-icon"><Factory size={16}/></span><span><b>North dock</b><small>Operations site</small></span><ChevronDown size={15}/></div>
      <div className="nav-label">WORKSPACE</div>
      <button className={`nav-item ${screen === 'overview' ? 'selected' : ''}`} onClick={() => setScreen('overview')}><Gauge size={17}/> Operations overview</button>
      <button className="nav-item" onClick={() => { setScreen('overview'); window.setTimeout(() => document.querySelector('#review')?.scrollIntoView({ behavior: 'smooth' }), 50); }}><Video size={17}/> Video review</button>
      <button className="nav-item" onClick={() => { setScreen('upload'); window.setTimeout(() => csvInput.current?.click(), 50); }}><Layers3 size={17}/> Change files <FileUp size={14} className="nav-trailing"/></button>
      <div className="side-bottom"><div className="privacy"><ShieldCheck size={16}/><span><b>Private by design</b><small>Files stay in this browser session</small></span></div><button className="nav-item"><Settings2 size={17}/> Settings</button><div className="profile"><div className="avatar">KM</div><span><b>Operations lead</b><small>Local workspace</small></span><ChevronDown size={14}/></div></div>
    </aside>}

    <main className={`main ${screen === 'upload' ? 'upload-main' : ''}`}>
      {screen !== 'upload' && <header className="topbar"><div className="crumb">Operations <span>/</span> Overview <span className="live-tag"><i/> OPERATIONS</span></div><div className="top-actions"><button className="icon-button" aria-label="Search"><Search size={17}/></button><button className="icon-button notification" aria-label="Notifications"><Bell size={17}/><i/></button><span className="top-date">Shift review · Today</span></div></header>}
      <div className={`content ${screen === 'upload' ? 'upload-content' : ''}`}>
        <input ref={csvInput} type="file" accept=".csv,text/csv" hidden onChange={(e) => { loadCSV(e.target.files?.[0]); e.target.value = ''; }}/>
        <input ref={videoInput} type="file" accept=".mp4,.webm,.mov,video/mp4,video/webm,video/quicktime" hidden onChange={(e) => { loadVideo(e.target.files?.[0]); e.target.value = ''; }}/>
        {screen === 'upload' && <section className="upload-welcome"><div className="brand"><div className="brand-mark"><ScanLine size={19}/></div><span>worksight<span className="brand-dot">.</span></span></div><div className="eyebrow">VIDEO OPERATIONS WORKSPACE</div><h1>{dataReady ? 'Your files are ready' : 'Upload your analysis files'}</h1><p>{dataReady ? `${csvName} and ${source} are validated for this browser session.` : 'Choose your WorkSight event CSV and browser-ready annotated video. Once both are validated, the operations overview opens automatically.'}</p>{dataReady && <button className="button primary upload-return" onClick={() => setScreen('overview')}>Return to operations overview</button>}</section>}
        {screen !== 'upload' && <section className="page-heading"><div><div className="eyebrow">SHIFT INTELLIGENCE <span className="green-pip"/></div><h1>Operations overview</h1><p>Review model-detected events against the annotated video.</p></div><div className="heading-buttons"><button className="button secondary" onClick={() => { setScreen('upload'); window.setTimeout(() => csvInput.current?.click(), 0); }}><FileUp size={15}/> Change files</button><button className="button primary" onClick={() => { setScreen('upload'); window.setTimeout(() => videoInput.current?.click(), 0); }}><Video size={15}/> Change video</button></div></section>}
        {screen !== 'upload' && <div className="data-notice"><span className="notice-icon"><Activity size={15}/></span><span>{notice}</span><button onClick={() => setNotice('Signals are estimates from video. Confirm important events before acting.') }><CircleHelp size={15}/></button></div>}
        {screen === 'upload' || !dataReady ? <section className="upload-gate">
          <div className="gate-heading"><span className="eyebrow">{dataReady ? 'FILES VALIDATED' : 'GET STARTED'}</span><h2>{dataReady ? 'Your files are ready' : 'Add both analysis files'}</h2><p>{dataReady ? 'Replace either file below, or return to the operations overview.' : 'The report opens after WorkSight validates the event CSV and confirms the annotated video plays and covers all event times.'}</p></div>
          <div className="file-checks">
            <article className={`file-check ${csvValid ? 'file-valid' : csvError && csvError !== 'Validating CSV…' ? 'file-invalid' : ''}`}><div className="file-check-top"><span className="file-check-icon"><Layers3 size={19}/></span><span className="file-number">01</span></div><h3>Events CSV</h3><p>WorkSight export with Time, Track, Signal, Duration (s), and Confidence.</p><button className="button secondary" onClick={() => csvInput.current?.click()}><FileUp size={14}/>{csvValid ? 'Choose another CSV' : 'Choose CSV file'}</button><div className="file-status">{csvValid ? <ShieldCheck size={14}/> : <CircleHelp size={14}/>}<span>{csvValid ? `${csvName} · ${events.length} valid events` : csvError || 'Required · CSV format not checked yet'}</span></div></article>
            <article className={`file-check ${videoValid ? 'file-valid' : videoError && videoError !== 'Checking video format and duration…' ? 'file-invalid' : ''}`}><div className="file-check-top"><span className="file-check-icon"><Video size={19}/></span><span className="file-number">02</span></div><h3>Video evidence</h3><p>Load the browser-ready annotated MP4 to see zones and detections at each event.</p><button className="button secondary" onClick={() => videoInput.current?.click()}><Video size={14}/>{videoValid ? 'Choose another video' : 'Choose video file'}</button><div className="file-status">{videoValid ? <ShieldCheck size={14}/> : <CircleHelp size={14}/>}<span>{videoValid ? `${source} · ${fmt(videoSeconds)} · playable` : videoError || 'Required · MP4, WebM, or MOV'}</span></div>{videoUrl && <video className="validation-player" ref={videoRef} src={videoUrl} controls playsInline preload="metadata" onLoadedMetadata={(e) => setVideoSeconds(e.currentTarget.duration || 0)} onCanPlay={markVideoReady} onError={() => { setVideoValid(false); setVideoError(`Browser cannot decode ${source}. Choose the H.264 *_annotated_browser.mp4 file.`); }}/>}</article>
          </div>
          {csvValid && videoValid && !timestampsMatch && <div className="validation-warning"><CircleHelp size={16}/> CSV/video mismatch: last event is at {fmt(latestEventTime)}, but video is {fmt(videoSeconds)}. Load the matching video.</div>}
          <div className="gate-footer"><ShieldCheck size={15}/><span>Files stay in this browser session. No report data appears until both files pass validation.</span></div>
        </section> : <>
        <section className="panel worker-activity-panel">
          <div className="panel-heading"><div><div className="eyebrow">01 · ACTIVITY BY MINUTE</div><h2>Detected handling and belt activity</h2><p>Each bar represents one 60-second video window. Select a bar to review that minute.</p></div></div>
          <div className="worker-activity-chart" aria-label="Detected activity by 60-second video window">
            {minuteActivity.map((window) => <button type="button" key={window.minute} onClick={() => seekTo(window.minute * 60, fmt(window.minute * 60))} title={`${fmt(window.minute * 60)}: ${window.seconds.toFixed(1)} seconds of detected activity`}>
              <em>{window.seconds === mostActivity ? 'MOST' : window.seconds === leastActivity ? 'LEAST' : ''}</em><i style={{ height: `${Math.max(8, Math.round(100 * window.seconds / maxMinuteActivity))}%` }}/><span>{fmt(window.minute * 60)}</span>
            </button>)}
            {minuteActivity.length === 0 && <p>No activity evidence is available in this export.</p>}
          </div>
        </section>
        <section className="topic-review" aria-label="Operational focus areas">
          <div className="topic-heading"><div><div className="eyebrow">OPERATIONS AT A GLANCE</div><h2>Five review areas</h2></div><span>Select an area to filter the event evidence</span></div>
          <div className="topic-card-grid">
            <button type="button" className={`topic-card ${selectedTopic === 'worker' ? 'topic-selected' : ''}`} onClick={() => showTopicDetails('worker')}>
              <span>Worker utilisation</span><b>{activeWorkerTracks} tracks with handling cues</b><small>Slowdown cues: {lowActivityEvents.length} across {lowActivityWorkerTracks} tracks · Available: {availableWorkerTracks} · Waiting: {waitingWorkerTracks}</small>
            </button>
            <button type="button" className={`topic-card ${selectedTopic === 'belt' ? 'topic-selected' : ''}`} onClick={() => showTopicDetails('belt')}>
              <span>Belt utilisation</span><b>{topicEvents.belt.length} interaction cues</b><small>Occupancy, throughput and capacity are not measured.</small>
            </button>
            <button type="button" className={`topic-card ${selectedTopic === 'resources' ? 'topic-selected' : ''}`} onClick={() => showTopicDetails('resources')}>
              <span>Resource allocation</span><b>{staffingMismatchEvents.length} staffing mismatch cues</b><small>Three or more workers present with no more than one candidate-active worker.</small>
            </button>
            <button type="button" className={`topic-card ${selectedTopic === 'bottlenecks' ? 'topic-selected' : ''}`} onClick={() => showTopicDetails('bottlenecks')}>
              <span>Operational bottlenecks</span><b>{topicEvents.bottlenecks.length} slowdown / waiting cues</b><small>Based on package detections without handling signals.</small>
            </button>
            <button type="button" className={`topic-card ${selectedTopic === 'risks' ? 'topic-selected' : ''}`} onClick={() => showTopicDetails('risks')}>
              <span>Risks & safety</span><b>{topicEvents.risks.length} safety cues</b><small>Person on conveyor / throwing are cues; falling and hitting are not detected.</small>
            </button>
          </div>
        </section>
        <section className="panel slowdown-panel" aria-label="Slowdown events by anonymous track">
          <div className="panel-heading"><div><div className="eyebrow">SLOWDOWN EVENTS</div><h2>Anonymous tracks ranked by candidate slowdown time</h2><p>Select a track to see its timestamps and open supporting video evidence.</p></div><span className="count-pill">{slowdownTracks.length} tracks</span></div>
          <div className="slowdown-grid">
            <div className="slowdown-track-list">
              {slowdownTracks.map((item, index) => <button type="button" key={item.track} className={activeSlowdownTrack?.track === item.track ? 'slowdown-track-selected' : ''} onClick={() => setSelectedSlowdownTrack(item.track)}><span>#{index + 1} · Person ID {item.track}</span><b>{fmt(item.seconds)}</b><small>{item.events.length} slowdown events</small></button>)}
              {slowdownTracks.length === 0 && <p>No slowdown events are available.</p>}
            </div>
            <div className="slowdown-detail">
              {activeSlowdownTrack ? <><div><div className="eyebrow">SELECTED PERSON</div><h3>Person ID {activeSlowdownTrack.track}</h3><p>{fmt(activeSlowdownTrack.seconds)} total candidate slowdown time across {activeSlowdownTrack.events.length} events.</p></div><div className="slowdown-timestamp-list">{activeSlowdownTrack.events.map((event) => <button type="button" key={event.id} onClick={() => seek(event)}><b>{event.label}</b><span>{event.duration.toFixed(1)} sec · candidate slowdown</span><Play size={13}/></button>)}</div></> : <p>Select an anonymous track to see timestamps.</p>}
            </div>
          </div>
          <div className="review-event-caveat">Person IDs are temporary tracker IDs. Candidate slowdown is a review cue, not confirmed idle time.</div>
        </section>
    
        <section className="video-review-grid" id="review">
        <section className="panel video-panel primary-video-panel">
          <div className="panel-heading">
            <div><div className="eyebrow">VIDEO EVIDENCE</div><h2>Annotated video</h2><p>Select an event below to jump to its timestamp.</p></div>
            <button className="small-button" onClick={() => videoInput.current?.click()}><Video size={14}/> Replace video</button>
          </div>
          <div className="video-frame">
            <video ref={videoRef} src={videoUrl} controls playsInline preload="metadata" onCanPlay={markVideoReady}
              onError={() => { setVideoValid(false); setVideoError(`Browser cannot decode ${source}. Choose the H.264 *_annotated_browser.mp4 file.`); }}
              onTimeUpdate={(e) => {
                const second = Math.floor(e.currentTarget.currentTime);
                setReviewSecond((current) => current === second ? current : second);
                const current = events.find((event) => Math.abs(event.seconds - e.currentTarget.currentTime) < 1.5);
                if (current) setActive(current.id);
              }}/>
            <div className="video-caption"><span className="video-live-dot"/> {source}</div>
          </div>
          {activeEvent && <div className="event-evidence-card">
            <b>{presentEvent(activeEvent).label} · {activeEvent.label} · Track {activeEvent.track}</b>
            <span>{activeEvent.evidence || presentEvent(activeEvent).explanation}</span>
            {activeEvent.interpretation && <small>{activeEvent.interpretation}</small>}
          </div>}
        </section>

        <aside className="panel minute-events-panel">
          <div className="panel-heading"><div><div className="eyebrow">CURRENT WINDOW</div><h2>{fmt(windowStart)}–{fmt(windowEnd)}</h2><p>{windowTracks.length} anonymous tracks with review evidence: {windowTracks.join(', ') || 'none'}.</p></div></div>
          <div className="minute-events-list">
            {windowEvents.map((event) => <button type="button" key={event.id} onClick={() => seek(event)}><b>{event.label} · {presentEvent(event).label}</b><span>Track {event.track} · {event.duration.toFixed(1)}s</span><small>{event.evidence || presentEvent(event).explanation}</small></button>)}
            {windowEvents.length === 0 && <p>No review events overlap this minute.</p>}
          </div>
        </aside>
        </section>

    

        <section className="panel event-panel compact-event-panel" aria-label="Events">
          <div className="panel-heading">
            <div><div className="eyebrow">REVIEW EVENTS</div><h2>Events and risk cues</h2><p>Labels describe what the model detected; verify the moment in the video.</p></div>
            <div className="event-actions"><span className="count-pill">{filteredEvents.length} events</span>{selectedTopic !== 'all' && <button className="small-button" onClick={() => setSelectedTopic('all')}>All events</button>}<button className="small-button" onClick={downloadReview}><Download size={14}/> Export CSV</button></div>
          </div>
          <div className="review-event-list">
            {filteredEvents.map((event) => {
              const presentation = presentEvent(event);
              const safetyLabel = presentation.type === 'safety-high' ? 'URGENT SAFETY REVIEW'
                : presentation.type === 'safety' ? 'SAFETY REVIEW'
                  : presentation.type === 'worker' || event.signal === 'candidate waiting full belt' ? 'WORKER STATUS'
                    : presentation.type === 'belt' ? 'BELT SIGNAL'
                      : presentation.type === 'operations' ? 'OPERATIONS' : 'EVENT';
              return <button type="button" key={event.id}
                className={`review-event-row review-event-${presentation.type} ${active === event.id ? 'event-selected' : ''}`}
                onClick={() => seek(event)}>
                <span className="review-event-time">{event.label}</span>
                <span className="review-event-content">
                  <span className="review-event-title"><b>{presentation.label}</b><i>{safetyLabel}</i></span>
                  <span className="review-event-explanation">{event.evidence || presentation.explanation}</span>
                  {event.interpretation && <span className="review-event-interpretation">{event.interpretation}</span>}
                  <span className="review-event-meta">Track {event.track} · {event.duration.toFixed(1)} sec · person detection score {Math.round(event.confidence * 100)}%</span>
                </span>
                <Play size={14}/>
              </button>;
            })}
            {filteredEvents.length === 0 && <div className="review-event-empty">No events were recorded for this review area.</div>}
          </div>
          <div className="review-event-caveat">“Idle” and risk labels are review candidates based on detections and zone signals, not confirmed actions or safety incidents.</div>
        </section>

        <section className="panel priority-panel">
          <div className="panel-heading"><div><div className="eyebrow">PRIORITISED REVIEW QUEUE</div><h2>Review the highest-impact evidence first</h2><p>Urgent safety, longest slowdown periods, then belt activity.</p></div></div>
          <div className="priority-list">
            {priorityEvents.map((event) => <button type="button" key={event.id} onClick={() => seek(event)}><span>{event.label}</span><b>{presentEvent(event).label}</b><small>Track {event.track} · {event.duration.toFixed(1)}s · {event.evidence || presentEvent(event).explanation}</small></button>)}
          </div>
        </section>
        {topicDetailsOpen && selectedTopic !== 'all' && <section className="panel topic-timeline-panel" ref={topicDetailsRef} tabIndex="-1">
          <div className="panel-heading"><div><div className="eyebrow">TOPIC EVENT TIMELINE</div><h2>{selectedTopicEvents.length} {selectedTopic} review events</h2><p>Each bar shows an event duration across the video. Select a bar to open that evidence.</p></div><button className="small-button" onClick={() => setTopicDetailsOpen(false)}>Collapse</button></div>
          <div className="topic-timeline-scale"><span>0:00</span><span>{fmt(timelineSeconds / 2)}</span><span>{fmt(timelineSeconds)}</span></div>
          <div className="topic-timeline">
            {selectedTopicSignals.map((signal) => <div className="topic-timeline-row" key={signal}><span>{presentEvent({ signal }).label}</span><div className="topic-timeline-track">{selectedTopicEvents.filter((event) => event.signal === signal).map((event) => <button type="button" key={event.id} className={`timeline-event timeline-${presentEvent(event).type}`} style={{ left: `${100 * event.seconds / timelineSeconds}%`, width: `${Math.max(0.5, 100 * event.duration / timelineSeconds)}%` }} title={`${event.label} · ${event.duration.toFixed(1)}s · Track ${event.track}`} onClick={() => seek(event)}/>)}</div></div>)}
          </div>
        </section>}
        </>}
      </div>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
