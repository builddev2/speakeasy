import '../styles/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { DockApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DockApp />
  </StrictMode>,
);
