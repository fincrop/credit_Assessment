'use client';

interface ApiEndpointDisplayProps {
    method: string;
    url: string;
}

export default function ApiEndpointDisplay({ method, url }: ApiEndpointDisplayProps) {
    const getMethodColor = (method: string) => {
        switch (method) {
            case 'POST': return 'bg-blue-600';
            case 'GET': return 'bg-green-600';
            case 'PUT': return 'bg-yellow-600';
            case 'DELETE': return 'bg-red-600';
            default: return 'bg-gray-600';
        }
    };

    return (
        <div className="flex items-center gap-3 bg-gray-50 border border-gray-200 rounded-lg px-4 py-3">
            <span className={`${getMethodColor(method)} text-white text-sm font-bold px-3 py-1 rounded`}>
                {method}
            </span>
            <span className="text-gray-700 font-mono text-sm break-all">{url}</span>
        </div>
    );
}
