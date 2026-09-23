import test from 'node:test';
import assert from 'node:assert/strict';
import {
  durationNativeUnit, durationOptions, preferredDurationUnit,
  durationDisplayValue, durationStoredValue,
} from '../gateway/dashboard/duration-units.mjs';

test('duration picker defaults to useful units without changing stored values', () => {
  assert.equal(preferredDurationUnit('default_timeout_seconds', 120), 'minutes');
  assert.equal(preferredDurationUnit('image_timeout_seconds', 600), 'minutes');
  assert.equal(preferredDurationUnit('quota_poll_seconds', 45), 'seconds');
  assert.equal(preferredDurationUnit('reference_cache_ttl_seconds', 86400), 'days');
  assert.equal(preferredDurationUnit('cleanup_interval_hours', 24), 'days');
  assert.deepEqual(durationOptions('cleanup_interval_hours'), ['hours', 'days']);
  assert.equal(durationNativeUnit('history_retention_days'), null);
});

test('units convert back to existing API integer units', () => {
  assert.equal(durationStoredValue('2', 'minutes'), 120);
  assert.equal(durationStoredValue('10', 'minutes'), 600);
  assert.equal(durationStoredValue('1.5', 'minutes'), 90);
  assert.equal(durationStoredValue('1', 'days'), 86400);
  assert.equal(durationStoredValue('1', 'days', 'hours'), 24);
  assert.equal(durationStoredValue('2', 'days', 'hours'), 48);
});

test('changing the display unit preserves native values and dirty-state equality', () => {
  for (const seconds of [5, 45, 90, 120, 125, 599, 600, 900, 86400]) {
    for (const unit of ['seconds', 'minutes', 'hours', 'days']) {
      const display = durationDisplayValue(seconds, unit);
      assert.equal(durationStoredValue(display, unit), seconds, `${seconds}s in ${unit}`);
    }
  }
});

test('blank and fractional native values cannot silently become zero or valid integers', () => {
  assert.equal(durationStoredValue('', 'minutes'), null);
  assert.ok(Number.isNaN(durationStoredValue('garbage', 'minutes')));
  assert.equal(Number.isSafeInteger(durationStoredValue('1.01', 'minutes')), false);
  assert.equal(Number.isSafeInteger(durationStoredValue('1.5', 'hours', 'hours')), false);
});
