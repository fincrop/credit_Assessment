'use client';

interface DataTableProps {
    data: Record<string, unknown>[] | Record<string, unknown>;
    title?: string;
}

export default function DataTable({ data, title }: DataTableProps) {
    // Handle empty data
    if (!data || (Array.isArray(data) && data.length === 0)) {
        return (
            <div className="text-center py-8 text-stone-500">
                No data available
            </div>
        );
    }

    // Convert single object to array
    const dataArray = Array.isArray(data) ? data : [data];

    // Get all unique keys from all objects
    const allKeys = new Set<string>();
    dataArray.forEach(item => {
        if (typeof item === 'object' && item !== null) {
            Object.keys(item).forEach(key => allKeys.add(key));
        }
    });
    const headers = Array.from(allKeys);

    // Format cell value for display
    const formatValue = (value: unknown): string => {
        if (value === null || value === undefined) return '-';
        if (typeof value === 'object') return JSON.stringify(value);
        return String(value);
    };

    // Format header for display (convert snake_case to Title Case)
    const formatHeader = (header: string): string => {
        return header
            .replace(/_/g, ' ')
            .replace(/\b\w/g, char => char.toUpperCase());
    };

    return (
        <div className="overflow-x-auto">
            {title && (
                <h4 className="text-lg font-semibold text-gray-800 mb-3">{title}</h4>
            )}
            <table className="min-w-full divide-y divide-gray-200 border border-gray-200 rounded-lg overflow-hidden">
                <thead className="bg-gradient-to-r from-green-50 to-green-100">
                    <tr>
                        {headers.map((header) => (
                            <th
                                key={header}
                                className="px-4 py-3 text-left text-xs font-semibold text-green-800 uppercase tracking-wider"
                            >
                                {formatHeader(header)}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-100">
                    {dataArray.map((row, rowIndex) => (
                        <tr key={rowIndex} className="hover:bg-gray-50 transition-colors">
                            {headers.map((header) => (
                                <td
                                    key={`${rowIndex}-${header}`}
                                    className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap"
                                >
                                    {formatValue((row as Record<string, unknown>)[header])}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
