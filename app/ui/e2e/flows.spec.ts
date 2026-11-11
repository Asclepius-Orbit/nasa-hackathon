// User-flow e2e: each test launches its own app on its own throwaway profile.
import { expect, test } from '@playwright/test';

import { launch, signInAs, tapPin, type Launched } from './helpers';

let ctx: Launched;

test.beforeEach(async () => {
  ctx = await launch();
});
test.afterEach(async () => {
  await ctx.close();
});

test('login: wrong PINs count down, the third locks the member out', async () => {
  const { page } = ctx;
  await page.getByRole('button', { name: /Elena Ruiz/ }).click();
  await expect(page.getByRole('heading', { name: 'PIN for Elena Ruiz' })).toBeVisible();
  await tapPin(page, '9999');
  await expect(page.getByRole('alert').filter({ hasText: 'That PIN does not match. 2 tries left.' })).toBeVisible();
  await tapPin(page, '9999');
  await expect(page.getByRole('alert').filter({ hasText: '1 try left.' })).toBeVisible();
  await tapPin(page, '9999');
  await expect(page.getByRole('alert').filter({ hasText: /^Too many tries/ })).toBeVisible();
});

test('login: the right PIN lands on Today with the level word as heading', async () => {
  const { page } = ctx;
  await signInAs(page, 'Elena Ruiz');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Amber');
});

test('new crew member: name, PIN twice, signed in', async () => {
  const { page } = ctx;
  await page.getByRole('button', { name: /New crew member/ }).click();
  await page.getByLabel('Your name').fill('Ana García');
  await page.getByRole('button', { name: 'Continue' }).click();
  await tapPin(page, '4321');
  await expect(page.getByRole('heading', { name: 'Enter the PIN again' })).toBeVisible();
  await tapPin(page, '4321');
  await expect(page.locator('#today-title')).toBeVisible();
  await expect(page.locator('header')).toContainText('Ana García');
});

test('new crew member: a hostile name renders as text, never as an element', async () => {
  const { page } = ctx;
  const hostile = '<img src=x onerror=alert(1)>';
  await page.getByRole('button', { name: /New crew member/ }).click();
  await page.getByLabel('Your name').fill(hostile);
  await page.getByRole('button', { name: 'Continue' }).click();
  await tapPin(page, '4321');
  await tapPin(page, '4321');
  await expect(page.locator('#today-title')).toBeVisible();
  await expect(page.locator('header')).toContainText(hostile);
  // Back on the login tiles the name is also rendered.
  await page.getByRole('button', { name: 'Lock', exact: true }).click();
  await expect(page.getByTestId('lock-view')).toBeVisible();
  expect(await page.locator('img').count()).toBe(0);
});

test('daily report: answer pain, stiffness, none of these, save', async () => {
  const { page } = ctx;
  await signInAs(page, 'Elena Ruiz');
  await page.getByRole('link', { name: /Report/ }).click();
  const save = page.getByRole('button', { name: 'Save daily report' });
  await expect(save).toBeDisabled();
  await page.getByRole('radiogroup', { name: 'How much does your back hurt right now?' }).getByText('3', { exact: true }).click();
  await page.getByRole('radiogroup', { name: 'How stiff does your back feel?' }).getByText('2', { exact: true }).click();
  await page.getByRole('button', { name: 'None of these' }).click();
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByRole('status').filter({ hasText: 'Daily report saved.' })).toBeVisible();
});

test('urgent from the login screen: Send needs a chip, then Queued, Escape closes', async () => {
  const { page } = ctx;
  await page.getByRole('button', { name: /Urgent symptom/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  const send = dialog.getByRole('button', { name: 'Send', exact: true });
  await expect(send).toBeDisabled();
  await dialog.getByRole('group', { name: 'Symptoms' }).getByRole('button').first().click();
  await expect(send).toBeEnabled();
  await send.click();
  await expect(dialog.getByText('Queued.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('urgent from Today works the same', async () => {
  const { page } = ctx;
  await signInAs(page, 'Elena Ruiz');
  await page.getByRole('button', { name: 'Urgent symptom' }).click();
  const dialog = page.getByRole('dialog');
  const send = dialog.getByRole('button', { name: 'Send', exact: true });
  await expect(send).toBeDisabled();
  await dialog.getByRole('group', { name: 'Symptoms' }).getByRole('button').first().click();
  await send.click();
  await expect(dialog.getByText('Queued.')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('session: Stop: pain lands on the stopped screen', async () => {
  const { page } = ctx;
  await signInAs(page, 'Elena Ruiz');
  await page.getByRole('link', { name: /Session/ }).click();
  await page.getByRole('button', { name: /Stop: pain/ }).click();
  await expect(page.getByRole('heading', { name: 'Session stopped for pain' })).toBeVisible();
});

test('lock: the strip Lock button hides health data until the PIN is entered', async () => {
  const { page } = ctx;
  await signInAs(page, 'Elena Ruiz');
  await page.getByRole('button', { name: 'Lock', exact: true }).click();
  await expect(page.getByTestId('lock-view')).toBeVisible();
  await expect(page.locator('#today-title')).toHaveCount(0);
  await tapPin(page, '1234');
  await expect(page.locator('#today-title')).toBeVisible();
});

test('profile: from Settings, fill the profile and save it', async () => {
  const { page } = ctx;
  await signInAs(page, 'Elena Ruiz');
  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByRole('button', { name: 'Open profile' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Profile' })).toBeVisible();
  const panel = page.getByRole('region', { name: 'Your profile' });
  await panel.getByLabel('Date of birth').fill('1988-04-12');
  await panel.getByRole('button', { name: 'Female' }).click();
  await panel.getByLabel('Standing height (cm)').fill('171');
  await panel.getByLabel('Body mass (kg)').fill('64.5');
  await panel.getByRole('button', { name: 'Save profile' }).click();
  await expect(panel.getByText('Saved')).toBeVisible();
});
