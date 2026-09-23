/**
 * Settings UI only. The API, saved configuration, environment variables and
 * comparisons continue using their original integer units.
 */
const SECONDS = Object.freeze({seconds: 1, minutes: 60, hours: 3600, days: 86400});
const SINGULAR = Object.freeze({seconds: 'second', minutes: 'minute', hours: 'hour', days: 'day'});

export function durationNativeUnit(key) {
  if (key.endsWith('_seconds')) return 'seconds';
  if (key === 'cleanup_interval_hours') return 'hours';
  return null;
}

export function durationOptions(key) {
  if (key === 'cleanup_interval_hours') return ['hours', 'days'];
  if (key === 'reference_cache_ttl_seconds') return ['seconds', 'minutes', 'hours', 'days'];
  if (durationNativeUnit(key)) return ['seconds', 'minutes'];
  return [];
}

export function preferredDurationUnit(key, stored) {
  const native = durationNativeUnit(key);
  if (!native || !Number.isSafeInteger(stored)) return native || 'seconds';
  const seconds = stored * SECONDS[native];
  const options = durationOptions(key);
  for (const unit of ['days', 'hours', 'minutes', 'seconds']) {
    if (options.includes(unit) && seconds >= SECONDS[unit] && seconds % SECONDS[unit] === 0) return unit;
  }
  return native;
}

export function durationDisplayValue(stored, unit, native = 'seconds') {
  if (!Number.isFinite(stored)) return '';
  const display = stored * SECONDS[native] / SECONDS[unit];
  // Shorten repeating decimals without changing their round-trip integer value.
  return String(Number(display.toPrecision(12)));
}

export function durationStoredValue(text, unit, native = 'seconds') {
  if (String(text).trim() === '') return null;
  const value = Number(text);
  if (!Number.isFinite(value) || !SECONDS[unit] || !SECONDS[native]) return NaN;
  const stored = value * SECONDS[unit] / SECONDS[native];
  const integer = Math.round(stored);
  // Decimal display rounding should not turn a previously valid 125-second
  // value into 124.99999999998. Non-integral real edits remain invalid.
  return Number.isSafeInteger(integer) && Math.abs(stored - integer) < 1e-6 ? integer : stored;
}

export function durationOptionLabel(unit) {
  return SINGULAR[unit] ? SINGULAR[unit][0].toUpperCase() + SINGULAR[unit].slice(1) + 's' : unit;
}
