export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="error-banner" role="alert">
      <strong style={{ display: "block", marginBottom: "0.25rem" }}>Request failed</strong>
      {message}
    </div>
  );
}
