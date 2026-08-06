import { Layout } from './components/layout/Layout';
import { DocumentsView } from './components/dashboard/DocumentsView';

import { useEffect } from 'react';
import { loadRegionConfig } from './utils/regionConfig';

function App() {
  // Load region config once on mount so getRegionConfig() is ready for all child components
  useEffect(() => { loadRegionConfig(); }, []);

  return (
    <Layout>
      <DocumentsView />
    </Layout>
  );
}

export default App;
