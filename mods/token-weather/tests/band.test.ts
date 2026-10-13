import { test, expect } from 'claude-code/testing';
import { register, setReadings, withBand } from '../hooks/token-weather.mjs';

const Box = (props: any) => ({ type: 'Box', props });
const Text = (props: any) => ({ type: 'Text', props });
const engine: any = { ui: { resolve: () => ({ Box, Text }) } };
const reading = [{ tokens: 120000, window: 1000000, percent: 12 }, { tokens: 180000, window: 1000000, percent: 18 }];

function text(node: any): string {
  if (node == null) return '';
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(text).join('');
  return text(node.props?.children);
}

test('keeps the other mods\' part of the band and adds the forecast after it', () => {
  setReadings(reading);
  const others = { type: 'Box', props: { children: 'AgentFleet notice' } };
  const drawn: any = withBand(engine, { props: { hasSurvey: false, bodyColumns: 100 } }, others);
  expect(drawn.props.flexDirection).toBe('column');
  expect(drawn.props.width).toBeUndefined();
  expect(drawn.props.children[0]).toBe(others);
  expect(text(drawn.props.children[1])).toContain('18% of context');
  expect(text(drawn.props.children[1])).toContain('last turns');
});

test('draws only the forecast when no other mod drew anything', () => {
  setReadings(reading);
  const drawn: any = withBand(engine, { props: { hasSurvey: false, bodyColumns: 100 } }, undefined);
  expect(drawn.props.flexDirection).toBe('row');
  expect(text(drawn)).toContain('Clear');
});

test('yields to a survey and draws nothing before the first reading', () => {
  const others = { type: 'Box', props: { children: 'other' } };
  setReadings(reading);
  expect(withBand(engine, { props: { hasSurvey: true, bodyColumns: 100 } }, others)).toBe(others);
  setReadings([]);
  expect(withBand(engine, { props: { hasSurvey: false, bodyColumns: 100 } }, others)).toBe(others);
});

test('reads the width from the render props', () => {
  setReadings(reading);
  const narrow: any = withBand(engine, { props: { hasSurvey: false, bodyColumns: 40 } }, undefined);
  expect(text(narrow)).not.toContain('last turns');
});

test('the render hook always passes the render on', async () => {
  const hooks: Record<string, any> = {};
  register((event: string, ...rest: any[]) => { hooks[event] = rest[rest.length - 1]; });
  setReadings(reading);
  let calls = 0;
  const others = { type: 'Box', props: { children: 'other' } };
  const drawn: any = await hooks['ui.render'](engine, { props: { hasSurvey: false, bodyColumns: 100 } }, async () => { calls += 1; return others; });
  expect(calls).toBe(1);
  expect(drawn.props.children[0]).toBe(others);
});
