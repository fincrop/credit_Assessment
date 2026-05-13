import { redirect } from 'next/navigation';

export default function RootPage() {
  // Redirect the root page to the agristack sandbox by default, 
  // as it is the first step in the data pipeline.
  redirect('/agristack');
}
