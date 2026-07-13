export function ScoreDial({score, provisional}: {score: number; provisional: boolean}) {
  return <div className="score-dial" style={{"--score": `${score * 3.6}deg`} as React.CSSProperties} aria-label={`Confidence score ${score} out of 100`}>
    <div><strong>{Math.round(score)}</strong><span>/100</span></div>
    <small>{provisional ? "provisional" : "confidence score"}</small>
  </div>;
}

