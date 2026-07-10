import '../styles/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { TrainingApp } from './App';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <TrainingApp />
  </StrictMode>,
);
