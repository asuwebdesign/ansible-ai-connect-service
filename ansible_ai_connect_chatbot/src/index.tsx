import React from 'react';

import ReactDOM from 'react-dom/client';
// import './index.css';
import { App } from './App';
import reportWebVitals from './reportWebVitals';
import '@patternfly/react-core/dist/styles/base.css';
// import '@patternfly/patternfly/patternfly-addons.css';

// Add your extension CSS below
import '@patternfly/virtual-assistant/dist/css/main.css';
const root = ReactDOM.createRoot(document.getElementById('root') as HTMLElement);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
