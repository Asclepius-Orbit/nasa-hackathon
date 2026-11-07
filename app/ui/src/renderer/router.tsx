import { createHashRouter, Navigate, type RouteObject } from 'react-router';

import { AuthGate } from './auth/AuthGate';
import { AppLayout } from './components/layout/AppLayout';
import { BaselineView } from './features/baseline/BaselineView';
import { FirstDayView } from './features/firstday/FirstDayView';
import { HistoryView } from './features/history/HistoryView';
import { HoustonView } from './features/houston/HoustonView';
import { LockView } from './features/lock/LockView';
import { LoginView } from './features/login/LoginView';
import { OswestryView } from './features/oswestry/OswestryView';
import { ReportView } from './features/report/ReportView';
import { SessionView } from './features/session/SessionView';
import { SettingsView } from './features/settings/SettingsView';
import { StoppedView } from './features/stopped/StoppedView';
import { TodayView } from './features/today/TodayView';

/** Route table. Login and lock sit outside AppLayout (no strip, no rail); everything else is framed. Each screen lives in features/<name>. */
export const routes: RouteObject[] = [
  {
    element: <AuthGate />,
    children: [
      { path: '/login', element: <LoginView /> },
      { path: '/lock', element: <LockView /> },
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/today" replace /> },
          { path: '/today', element: <TodayView /> },
          { path: '/report', element: <ReportView /> },
          { path: '/session', element: <SessionView /> },
          { path: '/stopped', element: <StoppedView /> },
          { path: '/history', element: <HistoryView /> },
          { path: '/settings', element: <SettingsView /> },
          { path: '/first-day', element: <FirstDayView /> },
          { path: '/baseline', element: <BaselineView /> },
          { path: '/oswestry', element: <OswestryView /> },
          { path: '/houston', element: <HoustonView /> },
          { path: '*', element: <Navigate to="/today" replace /> },
        ],
      },
    ],
  },
];

/** Hash router: the app is served from a file, so history routing is not available. */
export const createAppRouter = (): ReturnType<typeof createHashRouter> => createHashRouter(routes);
