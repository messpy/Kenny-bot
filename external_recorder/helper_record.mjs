import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import OpusScript from "opusscript";

const require = createRequire(import.meta.url);
const Eris = require("eris");

const token = process.env.DISCORD_TOKEN;
const guildId = process.env.GUILD_ID;
const voiceChannelId = process.env.VOICE_CHANNEL_ID;
const outputPath = process.env.OUTPUT_PATH;
const readyPath = process.env.READY_PATH;
const logPath = process.env.LOG_PATH;
const playOnStop = process.env.PLAY_ON_STOP === "1";

const requireEnv = [
  ["DISCORD_TOKEN", token],
  ["GUILD_ID", guildId],
  ["VOICE_CHANNEL_ID", voiceChannelId],
  ["OUTPUT_PATH", outputPath],
  ["READY_PATH", readyPath],
];

for (const [name, value] of requireEnv) {
  if (!value) {
    console.error(`${name} is required`);
    process.exit(1);
  }
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.mkdirSync(path.dirname(readyPath), { recursive: true });
if (logPath) fs.mkdirSync(path.dirname(logPath), { recursive: true });

const SAMPLE_RATE = 48000;
const CHANNELS = 2;
const BYTES_PER_SAMPLE = 2;
const decoder = new OpusScript(SAMPLE_RATE, CHANNELS, OpusScript.Application.AUDIO);

function log(message) {
  const line = `${new Date().toISOString()} ${message}\n`;
  if (logPath) fs.appendFileSync(logPath, line);
}

class WavWriter {
  constructor(filePath) {
    this.filePath = filePath;
    this.fd = fs.openSync(filePath, "w");
    this.bytesWritten = 0;
    fs.writeSync(this.fd, Buffer.alloc(44));
  }

  write(pcmBuffer) {
    if (!pcmBuffer?.length) return;
    fs.writeSync(this.fd, pcmBuffer);
    this.bytesWritten += pcmBuffer.length;
  }

  close() {
    const header = Buffer.alloc(44);
    header.write("RIFF", 0);
    header.writeUInt32LE(36 + this.bytesWritten, 4);
    header.write("WAVE", 8);
    header.write("fmt ", 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);
    header.writeUInt16LE(CHANNELS, 22);
    header.writeUInt32LE(SAMPLE_RATE, 24);
    header.writeUInt32LE(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE, 28);
    header.writeUInt16LE(CHANNELS * BYTES_PER_SAMPLE, 32);
    header.writeUInt16LE(BYTES_PER_SAMPLE * 8, 34);
    header.write("data", 36);
    header.writeUInt32LE(this.bytesWritten, 40);
    fs.writeSync(this.fd, header, 0, header.length, 0);
    fs.closeSync(this.fd);
  }
}

const client = new Eris.Client(token, {
  intents: ["guilds", "guildVoiceStates"],
});

let connection = null;
let receiver = null;
let writer = new WavWriter(outputPath);
let packetCount = 0;
let stopping = false;
let sessionStartedAt = Date.now();
let firstPacketAt = null;
let lastPacketAt = null;
let lastTimestamp = null;

function writeSilenceSamples(sampleCount) {
  if (!sampleCount || sampleCount <= 0) return;
  const bytes = sampleCount * CHANNELS * BYTES_PER_SAMPLE;
  writer.write(Buffer.alloc(bytes));
}

async function stopAndExit(code = 0) {
  if (stopping) return;
  stopping = true;
  try {
    if (receiver) receiver.removeAllListeners("data");
  } catch {}
  try {
    if (lastPacketAt !== null) {
      const trailingMs = Math.max(0, Date.now() - lastPacketAt);
      const trailingSamples = Math.round((trailingMs / 1000) * SAMPLE_RATE);
      writeSilenceSamples(trailingSamples);
    }
    writer?.close();
  } catch {}
  if (playOnStop && connection) {
    await new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        connection.removeListener("end", onEnd);
        connection.removeListener("error", onError);
        resolve();
      };
      const onEnd = () => finish();
      const onError = () => finish();
      connection.on("end", onEnd);
      connection.on("error", onError);
      try {
        connection.play(outputPath);
      } catch {
        finish();
      }
      setTimeout(finish, 120000);
    });
  }
  try {
    if (connection) {
      const guild = client.guilds.get(guildId);
      const channel = guild?.channels.get(voiceChannelId);
      if (channel) channel.leave();
    }
  } catch {}
  try {
    client.disconnect({ reconnect: false });
  } catch {}
  log(`stopped packets=${packetCount} output=${outputPath}`);
  process.exit(code);
}

function onData(data, userId, timestamp) {
  if (!Buffer.isBuffer(data) || !data.length) return;
  try {
    const now = Date.now();
    if (firstPacketAt === null) {
      firstPacketAt = now;
      const leadingMs = Math.max(0, firstPacketAt - sessionStartedAt);
      const leadingSamples = Math.round((leadingMs / 1000) * SAMPLE_RATE);
      writeSilenceSamples(leadingSamples);
    }
    if (lastTimestamp !== null && Number.isFinite(timestamp)) {
      const expectedStep = 960;
      const gapSamples = Math.max(0, Number(timestamp) - lastTimestamp - expectedStep);
      if (gapSamples > 0) {
        writeSilenceSamples(gapSamples);
      }
    }
    const pcm = decoder.decode(data);
    writer.write(pcm);
    packetCount += 1;
    lastPacketAt = now;
    if (Number.isFinite(timestamp)) {
      lastTimestamp = Number(timestamp);
    }
    if (packetCount <= 5 || packetCount % 50 === 0) {
      log(`packet user=${userId} len=${data.length} ts=${timestamp} pcm=${pcm.length} count=${packetCount}`);
    }
  } catch (error) {
    log(`decode failed: ${error}`);
  }
}

client.on("ready", async () => {
  try {
    const guild = client.guilds.get(guildId);
    const channel = guild?.channels.get(voiceChannelId);
    if (!channel) throw new Error(`voice channel not found: ${voiceChannelId}`);
    connection = await channel.join({ opusOnly: true, selfMute: false, selfDeaf: false });
    receiver = connection.receive("opus");
    receiver.on("data", onData);
    fs.writeFileSync(readyPath, outputPath);
    log(`ready channel=${voiceChannelId} output=${outputPath}`);
  } catch (error) {
    log(`start failed: ${error}`);
    await stopAndExit(1);
  }
});

client.on("error", (error) => {
  log(`client error: ${error}`);
});

process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  if (String(chunk).toLowerCase().includes("stop")) {
    void stopAndExit(0);
  }
});
process.stdin.on("end", () => {
  void stopAndExit(0);
});
process.on("SIGINT", () => {
  void stopAndExit(0);
});
process.on("SIGTERM", () => {
  void stopAndExit(0);
});

await client.connect();
