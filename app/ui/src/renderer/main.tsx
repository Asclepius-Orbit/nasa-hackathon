import '@fontsource/atkinson-hyperlegible-next/latin-400.css';
import '@fontsource/atkinson-hyperlegible-next/latin-500.css';
import '@fontsource/atkinson-hyperlegible-next/latin-700.css';
import '@fontsource/atkinson-hyperlegible-next/latin-800.css';
import './styles/tokens.css';
import './styles/global.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';

const container = document.getElementById('root');
if (container === null) throw new Error('Missing #root element');

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
