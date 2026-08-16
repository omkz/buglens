"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE_URL } from "@/lib/config";
import type { InvestigationEvidence } from "./types";

type RecorderState =
  | "idle"
  | "requesting_permission"
  | "recording"
  | "recorded"
  | "saving"
  | "error";

const MIME_CANDIDATES = [
  "video/webm;codecs=vp9,opus",
  "video/webm;codecs=vp8,opus",
  "video/webm",
  "video/mp4",
];

function chooseMimeType() {
  if (typeof MediaRecorder.isTypeSupported !== "function") return undefined;
  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type));
}

function recordingFilename(mimeType: string) {
  return mimeType.startsWith("video/mp4") ? "recording.mp4" : "recording.webm";
}

type EvidenceRecorderProps = {
  investigationId: string;
  onSaved: (evidence: InvestigationEvidence[]) => void;
};

export function EvidenceRecorder({
  investigationId,
  onSaved,
}: EvidenceRecorderProps) {
  const [recorderState, setRecorderState] = useState<RecorderState>("idle");
  const [includeMicrophone, setIncludeMicrophone] = useState(false);
  const [logs, setLogs] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamsRef = useRef<MediaStream[]>([]);
  const chunksRef = useRef<Blob[]>([]);
  const recordingRef = useRef<Blob | null>(null);
  const previewUrlRef = useRef<string | null>(null);
  const discardRef = useRef(false);
  const recordingFailedRef = useRef(false);
  const mountedRef = useRef(true);

  const stopTracks = useCallback(() => {
    for (const stream of streamsRef.current) {
      for (const track of stream.getTracks()) track.stop();
    }
    streamsRef.current = [];
  }, []);

  const clearRecording = useCallback(() => {
    recordingRef.current = null;
    setPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      previewUrlRef.current = null;
      return null;
    });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      discardRef.current = true;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      stopTracks();
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    };
  }, [stopTracks]);

  async function startRecording() {
    setError(null);
    clearRecording();
    setRecorderState("requesting_permission");
    chunksRef.current = [];
    discardRef.current = false;
    recordingFailedRef.current = false;

    try {
      if (!navigator.mediaDevices?.getDisplayMedia || !window.MediaRecorder) {
        throw new Error("Screen recording is not supported by this browser.");
      }
      const displayStream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
      });
      streamsRef.current = [displayStream];
      if (!mountedRef.current) {
        stopTracks();
        return;
      }

      let microphoneStream: MediaStream | null = null;
      if (includeMicrophone) {
        try {
          microphoneStream = await navigator.mediaDevices.getUserMedia({
            audio: true,
          });
          streamsRef.current.push(microphoneStream);
          if (!mountedRef.current) {
            stopTracks();
            return;
          }
        } catch {
          stopTracks();
          throw new Error(
            "Microphone permission was not granted. Turn off Include microphone and try again.",
          );
        }
      }

      const combinedStream = new MediaStream([
        ...displayStream.getVideoTracks(),
        ...(microphoneStream?.getAudioTracks() ?? []),
      ]);
      streamsRef.current.push(combinedStream);
      const mimeType = chooseMimeType();
      const recorder = new MediaRecorder(
        combinedStream,
        mimeType ? { mimeType } : undefined,
      );
      recorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        recordingFailedRef.current = true;
        stopTracks();
        if (mountedRef.current) {
          setError("The recording could not be completed. Please try again.");
          setRecorderState("error");
        }
      };
      recorder.onstop = () => {
        stopTracks();
        recorderRef.current = null;
        if (
          !mountedRef.current ||
          discardRef.current ||
          recordingFailedRef.current
        ) {
          chunksRef.current = [];
          return;
        }
        const type = recorder.mimeType || mimeType || "video/webm";
        const blob = new Blob(chunksRef.current, { type });
        chunksRef.current = [];
        if (blob.size === 0) {
          setError("The recording was empty. Please try again.");
          setRecorderState("error");
          return;
        }
        recordingRef.current = blob;
        setPreviewUrl((current) => {
          if (current) URL.revokeObjectURL(current);
          const next = URL.createObjectURL(blob);
          previewUrlRef.current = next;
          return next;
        });
        setRecorderState("recorded");
      };

      displayStream.getVideoTracks()[0]?.addEventListener(
        "ended",
        () => {
          if (recorder.state !== "inactive") recorder.stop();
        },
        { once: true },
      );
      recorder.start();
      setRecorderState("recording");
    } catch (caught) {
      stopTracks();
      if (mountedRef.current) {
        setError(
          caught instanceof Error
            ? caught.message
            : "Screen sharing permission was not granted.",
        );
        setRecorderState("error");
      }
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    else stopTracks();
  }

  function discardRecording() {
    discardRef.current = true;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    stopTracks();
    clearRecording();
    setError(null);
    setRecorderState("idle");
  }

  async function saveEvidence() {
    const recording = recordingRef.current;
    if (!recording && !logs.trim()) {
      setError("Add a recording or paste logs before saving.");
      setRecorderState("error");
      return;
    }

    setError(null);
    setRecorderState("saving");
    const formData = new FormData();
    if (recording) {
      formData.append(
        "recording",
        recording,
        recordingFilename(recording.type),
      );
    }
    if (logs.trim()) formData.append("logs", logs);

    try {
      const response = await fetch(
        `${API_BASE_URL}/investigations/${encodeURIComponent(investigationId)}/evidence`,
        { method: "POST", credentials: "include", body: formData },
      );
      const body = (await response.json().catch(() => null)) as
        | { evidence?: InvestigationEvidence[]; detail?: string }
        | null;
      if (!response.ok || !body?.evidence) {
        throw new Error(body?.detail ?? "Unable to save evidence.");
      }
      onSaved(body.evidence);
      clearRecording();
      setLogs("");
      setRecorderState("idle");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Unable to save evidence.",
      );
      setRecorderState(recording ? "recorded" : "error");
    }
  }

  const recordingActive =
    recorderState === "requesting_permission" || recorderState === "recording";

  return (
    <div className="flex flex-col gap-5 rounded-lg border border-zinc-800 bg-zinc-900/20 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input
            type="checkbox"
            checked={includeMicrophone}
            disabled={recordingActive || recorderState === "saving"}
            onChange={(event) => setIncludeMicrophone(event.target.checked)}
            className="size-4 accent-zinc-100"
          />
          Include microphone
        </label>

        {recorderState === "recording" ? (
          <button
            type="button"
            onClick={stopRecording}
            className="rounded-full bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-400"
          >
            Stop Recording
          </button>
        ) : (
          <button
            type="button"
            onClick={startRecording}
            disabled={recorderState === "requesting_permission" || recorderState === "saving"}
            className="rounded-full border border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-100 hover:border-zinc-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {recorderState === "requesting_permission"
              ? "Waiting for permission…"
              : previewUrl
                ? "Record Again"
                : "Start Recording"}
          </button>
        )}
      </div>

      {recorderState === "recording" && (
        <p className="text-sm text-red-400">Recording…</p>
      )}

      {previewUrl && (
        <div className="flex flex-col gap-3">
          <video
            controls
            src={previewUrl}
            className="max-h-96 w-full rounded-md bg-black"
          />
          <button
            type="button"
            onClick={discardRecording}
            className="self-start text-sm text-zinc-500 hover:text-zinc-200"
          >
            Discard recording
          </button>
        </div>
      )}

      <label className="flex flex-col gap-2 text-sm text-zinc-400">
        Logs (optional)
        <textarea
          rows={7}
          maxLength={100000}
          value={logs}
          disabled={recordingActive || recorderState === "saving"}
          onChange={(event) => setLogs(event.target.value)}
          placeholder="Paste relevant browser or application logs"
          className="rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2 font-mono text-xs text-zinc-200 placeholder:text-zinc-600 outline-none focus:border-zinc-600 disabled:opacity-60"
        />
      </label>

      {error && (
        <p className="rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-400">
          {error}
        </p>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={saveEvidence}
          disabled={recordingActive || recorderState === "saving" || (!previewUrl && !logs.trim())}
          className="rounded-full bg-zinc-50 px-4 py-2 text-sm font-medium text-black hover:bg-zinc-200 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {recorderState === "saving" ? "Saving…" : "Save Evidence"}
        </button>
      </div>
    </div>
  );
}
