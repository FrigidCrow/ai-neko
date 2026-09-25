'use strict';

const fs = require('node:fs');
const path = require('node:path');
const DEFAULTS = Object.freeze({ scale: 1, alwaysOnTop: true });
const WIDTH = 800;
const HEIGHT = 680;

function validatePreferences(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).some((key) => !['scale', 'alwaysOnTop'].includes(key))) {
    throw new Error('Invalid desktop preferences');
  }
  const result = {};
  if ('scale' in value) {
    if (!Number.isFinite(value.scale) || value.scale < 0.7 || value.scale > 1.35) {
      throw new Error('Scale must be between 0.7 and 1.35');
    }
    result.scale = value.scale;
  }
  if ('alwaysOnTop' in value) {
    if (typeof value.alwaysOnTop !== 'boolean') throw new Error('Invalid always-on-top setting');
    result.alwaysOnTop = value.alwaysOnTop;
  }
  return result;
}

function clampBounds(bounds, workAreas) {
  if (!workAreas.length) throw new Error('No display available');
  const valid = bounds && Number.isFinite(bounds.x) && Number.isFinite(bounds.y);
  const width = WIDTH;
  const height = HEIGHT;
  // Pick the display containing the character (right half), not a disconnected
  // monitor or an invisible sliver of the transparent left side.
  const center = valid ? { x: bounds.x + width * 0.75, y: bounds.y + height * 0.5 } : null;
  const area = (center && workAreas.find((a) => center.x >= a.x && center.x < a.x + a.width &&
    center.y >= a.y && center.y < a.y + a.height)) || workAreas[0];
  return {
    x: Math.round(Math.max(area.x, Math.min(valid ? bounds.x : area.x + area.width - width,
      area.x + Math.max(0, area.width - width)))),
    y: Math.round(Math.max(area.y, Math.min(valid ? bounds.y : area.y + area.height - height,
      area.y + Math.max(0, area.height - height)))),
    width: Math.min(width, area.width), height: Math.min(height, area.height),
  };
}

function safeFile(file) {
  try {
    const info = fs.lstatSync(file);
    if (!info.isFile() || info.isSymbolicLink() || info.nlink > 1) throw new Error('Unsafe preferences file');
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
}

function loadPreferences(directory) {
  const file = path.join(directory, 'preferences.json');
  try {
    safeFile(file);
    const value = JSON.parse(fs.readFileSync(file, 'utf8'));
    const prefs = validatePreferences({ scale: value.scale, alwaysOnTop: value.alwaysOnTop });
    return { ...DEFAULTS, ...prefs, bounds: value.bounds };
  } catch { return { ...DEFAULTS }; }
}

function savePreferences(directory, value) {
  const file = path.join(directory, 'preferences.json');
  safeFile(file);
  const temporary = `${file}.${process.pid}.tmp`;
  const descriptor = fs.openSync(temporary, 'wx', 0o600);
  try {
    fs.writeFileSync(descriptor, JSON.stringify(value) + '\n');
    fs.closeSync(descriptor);
    fs.renameSync(temporary, file);
  } catch (error) {
    try { fs.closeSync(descriptor); } catch {}
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
}

module.exports = { DEFAULTS, WIDTH, HEIGHT, validatePreferences, clampBounds, loadPreferences, savePreferences };
